"""Tests for ``searxng_smoke.py``.

The smoke itself needs a Docker daemon and four container starts; these tests
need neither. Every judgement in that module is a pure function over captured
output, and every Docker call goes through an injected runner, so the whole of
it is drivable from here — which is the only reason its failure branches are
covered at all. A smoke whose *failure* paths have never run is a smoke you
find out about on the day it should have gone red.

Three things get pinned beyond ordinary branch coverage, because each is a
property a plausible edit would quietly break:

* the blocking phases run on an ``--internal`` network. Drop that flag and
  every phase still passes, while the "hermetic" claim — no engine on the
  internet touched, so a throttled DuckDuckGo cannot fail a publish — becomes
  false. It is exactly the kind of change nothing else would notice.
* the budget probe reaches past upstream's ``API_MAX``. At or below it, the
  test passes with a limiter installed, which is the failure mode it exists to
  catch.
* the helper images are digest-pinned, like everything else this repository
  consumes.

test_mapping:
  searxng_smoke.py: tests/test_searxng_smoke.py
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import pytest

import searxng_smoke as smoke
from pipeline.orchestrator import _SEARXNG_ENGINES

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


class FakeDocker:
    """Records every ``docker`` argv and answers from scripted responses."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[str, list[smoke.CommandResult]] = {}
        self.default = smoke.CommandResult(0, "", "")

    def script(self, subcommand: str, *results: smoke.CommandResult) -> None:
        self.responses.setdefault(subcommand, []).extend(results)

    def __call__(self, argv: Sequence[str]) -> smoke.CommandResult:
        call = list(argv)
        self.calls.append(call)
        subcommand = call[1] if len(call) > 1 else ""
        queued = self.responses.get(subcommand)
        if queued:
            return queued.pop(0)
        return self.default

    def argv_for(self, subcommand: str) -> list[list[str]]:
        return [call for call in self.calls if len(call) > 1 and call[1] == subcommand]


def _harness(docker: FakeDocker) -> smoke.SearxngHarness:
    return smoke.SearxngHarness(
        image="forage-searxng:test",
        run=docker,
        log=lambda _message: None,
        sleep=lambda _seconds: None,
    )


def _curl(status: int, body: str) -> smoke.CommandResult:
    """A CommandResult shaped like the smoke's own curl invocation."""
    return smoke.CommandResult(0, f"{body}\n{status}", "")


_ENVELOPE = json.dumps(
    {"query": "q", "results": [], "unresponsive_engines": [["duckduckgo", "err"]]}
)
_ENVELOPE_WITH_RESULTS = json.dumps(
    {"query": "q", "results": [{"url": "https://example.test", "title": "t"}]}
)


# ---------------------------------------------------------------------------
# Constants that encode verified facts
# ---------------------------------------------------------------------------


class TestVerifiedUpstreamFacts:
    """The names and numbers this story went and measured."""

    def test_the_valkey_url_env_name_is_the_current_one(self) -> None:
        # THE finding this story exists to nail down. Upstream renamed the
        # setting family from redis to valkey; `redis.url` is marked deprecated
        # in searx/settings_defaults.py and searx/valkeydb.py warns on it.
        assert smoke.VALKEY_URL_ENV == "SEARXNG_VALKEY_URL"

    def test_the_deprecated_name_is_recorded_not_used(self) -> None:
        # It still works on the pinned digest, which is why it is worth naming:
        # a reader who finds SEARXNG_REDIS_URL in an older doc needs to know it
        # is not wrong, just superseded.
        assert smoke.DEPRECATED_VALKEY_URL_ENV == "SEARXNG_REDIS_URL"
        assert smoke.DEPRECATED_VALKEY_URL_ENV != smoke.VALKEY_URL_ENV

    def test_the_limiter_opt_in_is_an_env_var(self) -> None:
        assert smoke.LIMITER_ENV == "SEARXNG_LIMITER"

    def test_budget_probe_reaches_past_upstream_api_max(self) -> None:
        assert smoke.JSON_BUDGET_PROBES > smoke.UPSTREAM_API_MAX, (
            "The budget phase must make more requests than upstream's "
            "ip_limit.API_MAX, or it passes with a limiter installed — which "
            "is the exact configuration it exists to catch."
        )

    @pytest.mark.parametrize("image", [smoke.VALKEY_IMAGE, smoke.CLIENT_IMAGE])
    def test_helper_images_are_digest_pinned(self, image: str) -> None:
        assert _DIGEST_RE.search(image), (
            f"{image} is not digest-pinned. A gate whose supporting cast can "
            "move is not reproducible."
        )


# ---------------------------------------------------------------------------
# Pure evaluators
# ---------------------------------------------------------------------------


class TestSecretRefusal:
    def test_an_exit_1_naming_the_secret_passes(self) -> None:
        logs = "ERROR:searx.webapp: server.secret_key is not changed."
        assert smoke.evaluate_secret_refusal(True, 1, logs) == []

    def test_a_container_that_keeps_running_fails(self) -> None:
        failures = smoke.evaluate_secret_refusal(False, -1, "")
        assert len(failures) == 1
        assert "kept running" in failures[0]

    def test_a_zero_exit_fails(self) -> None:
        logs = "server.secret_key is not changed."
        failures = smoke.evaluate_secret_refusal(True, 0, logs)
        assert any("exited 0" in failure for failure in failures)

    def test_an_unexplained_refusal_fails(self) -> None:
        failures = smoke.evaluate_secret_refusal(True, 1, "some unrelated crash")
        assert any("never mentions" in failure for failure in failures)


class TestJsonEnvelope:
    def test_a_good_envelope_passes(self) -> None:
        assert smoke.evaluate_json_envelope(smoke.Probe(200, _ENVELOPE)) == []

    def test_a_bot_block_is_reported_as_such(self) -> None:
        failures = smoke.evaluate_json_envelope(smoke.Probe(429, "Too Many Requests"))
        assert len(failures) == 1
        assert "block page" in failures[0]

    def test_any_other_status_fails(self) -> None:
        failures = smoke.evaluate_json_envelope(smoke.Probe(500, "boom"))
        assert any("HTTP 500" in failure for failure in failures)

    def test_a_connection_failure_fails(self) -> None:
        assert smoke.evaluate_json_envelope(smoke.Probe(0, "curl exited 7")) != []

    def test_html_returned_with_a_200_fails(self) -> None:
        # The nastiest shape: a block page that does not use a 4xx.
        failures = smoke.evaluate_json_envelope(smoke.Probe(200, "<html>blocked"))
        assert any("not JSON" in failure for failure in failures)

    def test_a_json_array_fails(self) -> None:
        failures = smoke.evaluate_json_envelope(smoke.Probe(200, "[1, 2]"))
        assert any("not an object" in failure for failure in failures)

    def test_a_missing_results_key_fails(self) -> None:
        failures = smoke.evaluate_json_envelope(smoke.Probe(200, '{"query": "q"}'))
        assert any("no 'results' key" in failure for failure in failures)


class TestJsonBudget:
    def test_all_two_hundreds_pass(self) -> None:
        assert smoke.evaluate_json_budget([200] * smoke.JSON_BUDGET_PROBES) == []

    def test_one_refusal_fails_and_names_the_request(self) -> None:
        statuses = [200, 200, 200, 200, 429, 429]
        failures = smoke.evaluate_json_budget(statuses)
        assert len(failures) == 1
        assert "first at request 5" in failures[0]
        assert str(smoke.UPSTREAM_API_MAX) in failures[0]


class TestLimiterBacked:
    def test_the_measured_shape_passes(self) -> None:
        assert (
            smoke.evaluate_limiter_backed(
                "no errors here",
                smoke.Probe(429, "Too Many Requests"),
                smoke.Probe(200, _ENVELOPE),
                3,
            )
            == []
        )

    def test_the_inert_marker_fails_even_with_a_url_set(self) -> None:
        failures = smoke.evaluate_limiter_backed(
            f"ERROR {smoke.INERT_LIMITER_MARKER}",
            smoke.Probe(429, ""),
            smoke.Probe(200, _ENVELOPE),
            3,
        )
        assert any("did not reach the backend" in failure for failure in failures)

    def test_an_empty_valkey_fails(self) -> None:
        failures = smoke.evaluate_limiter_backed(
            "", smoke.Probe(429, ""), smoke.Probe(200, _ENVELOPE), 0
        )
        assert any("Valkey DB is empty" in failure for failure in failures)

    def test_an_unreadable_valkey_fails(self) -> None:
        # `valkey_keys()` returns -1 when the DBSIZE call itself failed; a
        # `<= 0` check is what stops that reading as "not empty".
        failures = smoke.evaluate_limiter_backed(
            "", smoke.Probe(429, ""), smoke.Probe(200, _ENVELOPE), -1
        )
        assert any("Valkey DB is empty" in failure for failure in failures)

    def test_a_served_api_client_fails(self) -> None:
        failures = smoke.evaluate_limiter_backed(
            "", smoke.Probe(200, _ENVELOPE), smoke.Probe(200, _ENVELOPE), 3
        )
        assert any("expected 429" in failure for failure in failures)

    def test_a_blocked_browser_probe_invalidates_the_phase(self) -> None:
        failures = smoke.evaluate_limiter_backed(
            "", smoke.Probe(429, ""), smoke.Probe(429, ""), 3
        )
        assert any("proves nothing" in failure for failure in failures)


class TestLimiterInert:
    def test_the_measured_shape_passes(self) -> None:
        logs = f"ERROR:searx.limiter: {smoke.INERT_LIMITER_MARKER}"
        assert smoke.evaluate_limiter_inert(logs, smoke.Probe(200, _ENVELOPE), 0) == []

    def test_a_silent_inert_limiter_fails(self) -> None:
        # The whole hazard round 2 identified: unthrottled *and* quiet.
        failures = smoke.evaluate_limiter_inert("", smoke.Probe(200, _ENVELOPE), 0)
        assert any("did not log" in failure for failure in failures)

    def test_a_throttling_unbacked_limiter_fails(self) -> None:
        logs = smoke.INERT_LIMITER_MARKER
        failures = smoke.evaluate_limiter_inert(logs, smoke.Probe(429, ""), 0)
        assert any("supposed to be inert" in failure for failure in failures)

    def test_leftover_valkey_state_fails_the_phase(self) -> None:
        logs = smoke.INERT_LIMITER_MARKER
        failures = smoke.evaluate_limiter_inert(logs, smoke.Probe(200, _ENVELOPE), 4)
        assert any("not isolated" in failure for failure in failures)


class TestEnabledEngines:
    def test_disabled_entries_are_excluded(self) -> None:
        settings = {
            "engines": [
                {"name": "duckduckgo", "disabled": False},
                {"name": "bing", "disabled": True},
                {"name": "mojeek"},
                "not-a-mapping",
                {"engine": "nameless"},
            ]
        }
        assert smoke.enabled_engines(settings) == ["duckduckgo", "mojeek"]

    def test_a_missing_engines_key_is_empty(self) -> None:
        assert smoke.enabled_engines({}) == []

    def test_the_real_config_matches_the_orchestrator(self) -> None:
        # The live half of the parity assertion in tests/test_searxng_docker.py:
        # the advisory probe asks for whatever the baked config enables, so the
        # two must be the same set or the probe tests the wrong engines.
        assert set(smoke.load_enabled_engines()) == {
            name.strip() for name in _SEARXNG_ENGINES.split(",")
        }


class TestLiveResults:
    def test_results_pass(self) -> None:
        probe = smoke.Probe(200, _ENVELOPE_WITH_RESULTS)
        assert smoke.evaluate_live_results(probe, ["duckduckgo"]) == []

    def test_an_empty_result_list_is_reported_with_the_engine_errors(self) -> None:
        failures = smoke.evaluate_live_results(smoke.Probe(200, _ENVELOPE), ["mojeek"])
        assert len(failures) == 1
        assert "no engine returned a result" in failures[0]
        assert "duckduckgo" in failures[0]  # from unresponsive_engines

    def test_an_envelope_failure_short_circuits(self) -> None:
        failures = smoke.evaluate_live_results(smoke.Probe(429, "nope"), ["mojeek"])
        assert any("block page" in failure for failure in failures)


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


class TestProbe:
    def test_status_and_body_are_split_off_the_curl_output(self) -> None:
        docker = FakeDocker()
        docker.script("run", _curl(200, _ENVELOPE))
        probe = _harness(docker).probe("/search?format=json")
        assert probe.status == 200
        assert probe.body == _ENVELOPE

    def test_a_curl_that_never_answered_reports_status_zero(self) -> None:
        docker = FakeDocker()
        docker.script("run", smoke.CommandResult(7, "", "curl: (7) refused"))
        probe = _harness(docker).probe("/healthz")
        assert probe.status == 0
        assert "curl exited 7" in probe.body

    def test_the_client_runs_on_the_smoke_network_as_a_container(self) -> None:
        docker = FakeDocker()
        docker.script("run", _curl(200, "{}"))
        harness = _harness(docker)
        harness.probe("/healthz")
        (argv,) = docker.argv_for("run")
        assert "--network" in argv
        assert argv[argv.index("--network") + 1] == harness.network
        assert smoke.CLIENT_IMAGE in argv
        # A container, not the runner: Docker silently ignores `-p` on an
        # internal network, so there is no published port to curl from outside.
        assert "-p" not in argv

    def test_headers_are_passed_through(self) -> None:
        docker = FakeDocker()
        docker.script("run", _curl(200, "{}"))
        _harness(docker).probe("/healthz", headers=smoke.BROWSER_HEADERS)
        (argv,) = docker.argv_for("run")
        rendered = " ".join(argv)
        assert "Accept-Language: en-US,en;q=0.9" in rendered

    def test_the_api_client_probe_sends_no_browser_headers(self) -> None:
        # The point of the API-shaped probe is that it is *not* a browser.
        assert smoke.API_CLIENT_HEADERS == ()


class TestSearchPath:
    def test_the_query_asks_for_json(self) -> None:
        path = _harness(FakeDocker()).search_path()
        assert "format=json" in path
        assert "engines=" not in path

    def test_engines_are_appended_when_given(self) -> None:
        path = _harness(FakeDocker()).search_path(engines=["duckduckgo", "mojeek"])
        assert "engines=duckduckgo,mojeek" in path


class TestLifecycle:
    def test_create_network_passes_internal_through(self) -> None:
        docker = FakeDocker()
        _harness(docker).create_network(internal=True)
        create = [call for call in docker.argv_for("network") if "create" in call]
        assert create and "--internal" in create[0]

    def test_the_live_network_is_not_internal(self) -> None:
        docker = FakeDocker()
        _harness(docker).create_network(internal=False)
        create = [call for call in docker.argv_for("network") if "create" in call]
        assert create and "--internal" not in create[0]

    def test_a_failed_network_create_is_fatal(self) -> None:
        docker = FakeDocker()
        docker.script("network", smoke.CommandResult(0, "", ""))
        docker.script("network", smoke.CommandResult(1, "", "already exists"))
        with pytest.raises(RuntimeError, match="could not create network"):
            _harness(docker).create_network(internal=True)

    def test_env_reaches_the_container(self) -> None:
        docker = FakeDocker()
        _harness(docker).start_searxng({smoke.SECRET_ENV: "s", "OTHER": "v"})
        run_calls = [
            call for call in docker.argv_for("run") if smoke.SEARXNG_CONTAINER in call
        ]
        assert run_calls
        rendered = " ".join(run_calls[0])
        assert f"{smoke.SECRET_ENV}=s" in rendered
        assert "OTHER=v" in rendered

    def test_a_failed_start_is_fatal(self) -> None:
        docker = FakeDocker()
        docker.script("rm", smoke.CommandResult(0, "", ""))
        docker.script("run", smoke.CommandResult(125, "", "no such image"))
        with pytest.raises(RuntimeError, match="could not start"):
            _harness(docker).start_searxng({})

    def test_teardown_removes_both_containers_and_the_network(self) -> None:
        docker = FakeDocker()
        harness = _harness(docker)
        harness.teardown()
        removed = {call[-1] for call in docker.argv_for("rm")}
        assert {smoke.SEARXNG_CONTAINER, smoke.VALKEY_CONTAINER} <= removed
        assert any("rm" in call for call in docker.argv_for("network"))

    def test_container_state_parses_status_and_exit_code(self) -> None:
        docker = FakeDocker()
        docker.script("inspect", smoke.CommandResult(0, "exited 1\n", ""))
        assert _harness(docker).container_state("x") == ("exited", 1)

    def test_a_missing_container_reports_missing(self) -> None:
        docker = FakeDocker()
        docker.script("inspect", smoke.CommandResult(1, "", "No such object"))
        assert _harness(docker).container_state("x") == ("missing", -1)

    def test_valkey_keys_reads_dbsize(self) -> None:
        docker = FakeDocker()
        docker.script("exec", smoke.CommandResult(0, "7\n", ""))
        assert _harness(docker).valkey_keys() == 7

    def test_an_unreadable_dbsize_is_minus_one(self) -> None:
        docker = FakeDocker()
        docker.script("exec", smoke.CommandResult(1, "", "connection refused"))
        assert _harness(docker).valkey_keys() == -1


class TestReadiness:
    def test_a_two_hundred_healthz_is_ready(self) -> None:
        docker = FakeDocker()
        docker.script("inspect", smoke.CommandResult(0, "running 0\n", ""))
        docker.script("run", _curl(200, ""))
        assert _harness(docker).wait_until_ready() is True

    def test_an_exited_container_stops_the_wait_immediately(self) -> None:
        # Without this the readiness loop burns the whole budget polling a
        # container that is never coming back, and the phase reports a timeout
        # instead of a crash.
        docker = FakeDocker()
        docker.script("inspect", smoke.CommandResult(0, "exited 1\n", ""))
        assert _harness(docker).wait_until_ready() is False
        assert docker.argv_for("run") == []

    def test_the_budget_is_honoured(self) -> None:
        docker = FakeDocker()
        docker.default = smoke.CommandResult(0, "running 0\n", "")
        clock = iter([0.0, 0.0, 10_000.0, 10_000.0])
        harness = smoke.SearxngHarness(
            image="i",
            run=docker,
            log=lambda _m: None,
            sleep=lambda _s: None,
            monotonic=lambda: next(clock),
        )
        assert harness.wait_until_ready() is False

    def test_wait_until_exited_returns_the_code(self) -> None:
        docker = FakeDocker()
        docker.script("inspect", smoke.CommandResult(0, "exited 1\n", ""))
        assert _harness(docker).wait_until_exited() == (True, 1)

    def test_wait_until_exited_gives_up(self) -> None:
        docker = FakeDocker()
        docker.default = smoke.CommandResult(0, "running 0\n", "")
        clock = iter([0.0, 10_000.0])
        harness = smoke.SearxngHarness(
            image="i",
            run=docker,
            log=lambda _m: None,
            sleep=lambda _s: None,
            monotonic=lambda: next(clock),
        )
        exited, _code = harness.wait_until_exited()
        assert exited is False


class TestPhaseWiring:
    def test_the_blocking_run_creates_a_network_with_no_egress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # THE guard for the hermeticity claim, and it drives
        # `run_blocking_smoke` rather than `create_network` — which is the
        # whole point.
        #
        # Written the obvious way first, this test called
        # `harness.create_network(internal=True)` itself and asserted the flag
        # came through. That tests the *method*: a mutation flipping the
        # argument at the one call site that matters passed the entire suite.
        # The hermeticity claim is what lets this smoke sit inside a publish
        # `needs:` chain at all — without `--internal`, every phase still goes
        # green while a throttled DuckDuckGo can fail a release.
        #
        # The phases are emptied rather than simulated: what is under test is
        # the wiring, and four faked container lifecycles would only add ways
        # for this test to fail for the wrong reason.
        monkeypatch.setattr(smoke, "BLOCKING_PHASES", ())
        docker = FakeDocker()
        assert smoke.run_blocking_smoke(_harness(docker)) == []
        create = [call for call in docker.argv_for("network") if "create" in call]
        assert create, "run_blocking_smoke created no network"
        assert "--internal" in create[0], (
            "run_blocking_smoke created a network with egress. Every phase "
            "would still pass, and the hermeticity claim this lane rests on "
            "would silently be false."
        )
        assert any(smoke.VALKEY_CONTAINER in call for call in docker.argv_for("run")), (
            "run_blocking_smoke must start the Valkey the limiter phases need"
        )

    def test_the_live_run_creates_a_network_with_egress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _no_phase(_harness_arg: smoke.SearxngHarness) -> list[str]:
            return []

        monkeypatch.setattr(smoke, "phase_live_engines", _no_phase)
        docker = FakeDocker()
        assert smoke.run_live_smoke(_harness(docker)) == []
        create = [call for call in docker.argv_for("network") if "create" in call]
        assert create and "--internal" not in create[0], (
            "The advisory probe must reach real engines; that is its whole job."
        )

    def test_every_blocking_phase_is_registered(self) -> None:
        assert (
            smoke.phase_secret_required,
            smoke.phase_envelope_and_budget,
            smoke.phase_limiter_backed,
            smoke.phase_limiter_inert,
        ) == smoke.BLOCKING_PHASES, (
            "A phase dropped from this tuple stops running and nothing else "
            "reports it — the smoke would simply get faster and stay green."
        )

    def test_the_envelope_phase_supplies_a_backend_it_does_not_enable(self) -> None:
        # A Valkey URL with no SEARXNG_LIMITER must not switch a limiter on.
        # If it ever did, the shipped default would be throttled in any
        # deployment that also runs a cache.
        docker = FakeDocker()
        docker.script("inspect", smoke.CommandResult(0, "running 0\n", ""))
        docker.script("run", smoke.CommandResult(0, "cid\n", ""))  # start
        docker.script("run", _curl(200, ""))  # /healthz
        docker.script("run", _curl(200, _ENVELOPE))  # envelope
        for _ in range(smoke.JSON_BUDGET_PROBES):
            docker.script("run", _curl(200, _ENVELOPE))
        assert smoke.phase_envelope_and_budget(_harness(docker)) == []
        start = docker.argv_for("run")[0]
        rendered = " ".join(start)
        assert smoke.VALKEY_URL_ENV in rendered
        assert smoke.LIMITER_ENV not in rendered
