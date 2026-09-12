"""Guards on the three governance documents `feature-forage-contract` US-003 adds.

``contract/GOVERNANCE.md``, ``SECURITY.md`` and ``.github/pull_request_template.md``
are prose, and prose about a moving codebase rots silently. Every assertion below
exists because the sentence it checks makes a *mechanical* claim — a version, a
command, a file path, a count, a list of required checks — and a mechanical claim
in a document nobody executes is a claim that stops being true without anything
going red.

Three ties are worth knowing about before reading the rest:

* GOVERNANCE's "current contract version is **X.Y.Z**" line is checked against
  ``pipeline.contract.CONTRACT_VERSION``. A bump that leaves the doc behind fails
  here, which is the whole point of writing the number down at all;
* the regeneration command both GOVERNANCE and the PR template tell an author to
  run is compared against ``scripts.export_contract.REGEN_COMMAND`` rather than
  spelled out twice. A renamed module breaks the docs' instruction, and this is
  what notices;
* the PR template's "six required checks" line is compared against the jobs
  ``publish`` actually hangs off in ``.github/workflows/ci.yml``. A seventh gate
  that never reaches the template would leave the document telling contributors a
  shorter truth than the workflow enforces.

What these tests deliberately do **not** do is assert that the documents *say*
particular sentences about judgement calls. A worked example's classification is
checked for being present and unambiguous (exactly one class per row, drawn from
the four the table defines); whether MAJOR was the right call for row 4 is a human
question, recorded in the doc and its cited sources.

test_mapping:
  contract/GOVERNANCE.md: tests/test_governance_docs.py
  SECURITY.md: tests/test_governance_docs.py
  .github/pull_request_template.md: tests/test_governance_docs.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from pipeline.contract import CONTRACT_VERSION
from pipeline.sanitizer_revision import _REVISION_SOURCES
from scripts.export_contract import ANCHOR_PATH, CONTRACT_PATH, REGEN_COMMAND

_REPO_ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE_PATH = _REPO_ROOT / "contract" / "GOVERNANCE.md"
SECURITY_PATH = _REPO_ROOT / "SECURITY.md"
PR_TEMPLATE_PATH = _REPO_ROOT / ".github" / "pull_request_template.md"
CI_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

_DOCUMENTS = (GOVERNANCE_PATH, SECURITY_PATH, PR_TEMPLATE_PATH)

# The four classes the governance table defines. A worked example that names
# none of them, or more than one, is not answerable without a human.
_CLASSES = ("MAJOR", "MINOR", "PATCH", "no bump")

# The six classification examples the spec's Independent Test requires the
# document to answer on its own. Each entry is (marker, expected classes) —
# markers are the distinctive words of the example, not whole sentences, so a
# reworded table row still matches while a *missing* row does not.
_SIX_EXAMPLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Remove a response field", ("MAJOR",)),
    ("Add an optional request parameter", ("MINOR",)),
    ("Fix a description", ("PATCH", "no bump")),
    ("Change an enum member's meaning", ("MAJOR",)),
    ("A schema-doc tightening", ("no bump", "PATCH")),
    ("An urgent security tightening", ("MINOR",)),
)

# The five rulings this epic recorded, by the heading marker each section
# carries. (a2) is US-001's verification finding and is listed separately from
# (a) precisely because it is a different ruling about a different thing.
_RULING_MARKERS = ("### (a) ", "### (a2) ", "### (b) ", "### (c) ", "### (d) ")

# Counts these documents state in words. Both are read back out of the code —
# the hashed-source count from `_REVISION_SOURCES`, the required-check count
# from the jobs `publish` hangs off — so a number that goes stale is a red test
# rather than a confidently wrong instruction.
_NUMBER_WORDS = {
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_TABLE_ROW_RE = re.compile(r"^\|\s*(\d)\s*\|")


@pytest.fixture(scope="module")
def governance() -> str:
    return GOVERNANCE_PATH.read_text()


@pytest.fixture(scope="module")
def security() -> str:
    return SECURITY_PATH.read_text()


@pytest.fixture(scope="module")
def pr_template() -> str:
    return PR_TEMPLATE_PATH.read_text()


def _section(text: str, heading: str) -> str:
    """The body of one section, from its heading to the next of equal depth."""
    lines = text.splitlines()
    depth = len(heading) - len(heading.lstrip("#"))
    starts = [index for index, line in enumerate(lines) if line.startswith(heading)]
    assert len(starts) == 1, f"Expected exactly one {heading!r} heading; got {starts}"
    start = starts[0]
    for index in range(start + 1, len(lines)):
        stripped = lines[index]
        if (
            stripped.startswith("#")
            and len(stripped) - len(stripped.lstrip("#")) <= depth
        ):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def _cells(row: str) -> list[str]:
    """The cells of one markdown table row, outer pipes dropped."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _classes_in(row: str) -> list[str]:
    """Which of the four classes the row's **Class** column names.

    The column, not the whole row: a row may legitimately mention another class
    while explaining itself — row 4 recommends adding a new enum member
    (a MINOR) instead of redefining an old one (the MAJOR it classifies), and
    row 6 explains that it is a MAJOR-shaped change shipped as a MINOR. Reading
    the whole row would make both of them ambiguous when neither is.
    """
    cells = _cells(row)
    assert len(cells) >= 3, f"Malformed worked-example row: {row!r}"
    return [name for name in _CLASSES if name in cells[2]]


class TestTheDocumentsExist:
    """The three artifacts US-003 owes, at the paths the spec names."""

    @pytest.mark.parametrize("path", _DOCUMENTS, ids=lambda p: p.name)
    def test_document_exists_and_is_not_a_stub(self, path: Path) -> None:
        assert path.is_file(), f"{path} is missing"
        assert len(path.read_text().splitlines()) > 20, (
            f"{path} is a stub. A governance document nobody can answer a "
            "question from is worse than none — it looks like the question was "
            "already settled."
        )


class TestGovernanceStaysTiedToTheCode:
    """The claims in GOVERNANCE.md that the code can contradict."""

    def test_it_states_the_current_contract_version(self, governance: str) -> None:
        stated = re.search(
            r"current contract version is \*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", governance
        )
        assert stated is not None, (
            "GOVERNANCE.md must state the current contract version in the form "
            "`current contract version is **X.Y.Z**` — it is the one number in "
            "the document a reader will take on trust, so it is the one that "
            "has to be checked."
        )
        assert stated.group(1) == CONTRACT_VERSION, (
            f"GOVERNANCE.md says the contract is {stated.group(1)}; "
            f"pipeline/contract.py says {CONTRACT_VERSION}. Step 6 of the "
            "document's own bump procedure is updating this line."
        )

    def test_it_names_the_regeneration_command_exactly(self, governance: str) -> None:
        assert REGEN_COMMAND in governance, (
            f"GOVERNANCE.md must name the regeneration command {REGEN_COMMAND!r} "
            "verbatim — a bump procedure whose command has drifted sends the "
            "author looking for a script that is no longer there."
        )

    def test_it_names_the_generated_artifacts_by_path(self, governance: str) -> None:
        for path in (CONTRACT_PATH, ANCHOR_PATH):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            assert relative in governance, (
                f"GOVERNANCE.md does not mention {relative} — it governs that "
                "file, and a consumer reads it to learn which bytes are the "
                "contract."
            )

    def test_every_relative_link_resolves(self) -> None:
        # A document whose cross-references 404 is a document people stop
        # following. Anchors are stripped: the target file's existence is what
        # is checkable here.
        broken: list[str] = []
        for path in _DOCUMENTS:
            for target in _MARKDOWN_LINK_RE.findall(path.read_text()):
                link = str(target).split("#", 1)[0]
                if not link or link.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (path.parent / link).resolve().exists():
                    broken.append(f"{path.name} -> {link}")
        assert broken == [], f"Broken relative links: {broken}"


@pytest.fixture(scope="module")
def example_rows(governance: str) -> list[str]:
    """The numbered rows of GOVERNANCE's worked-examples table."""
    table = _section(governance, "## The six worked examples")
    return [line for line in table.splitlines() if _TABLE_ROW_RE.match(line)]


class TestTheSixWorkedExamples:
    """The Independent Test: six classifications, answerable from the doc alone."""

    def test_there_are_exactly_six(self, example_rows: list[str]) -> None:
        assert len(example_rows) == 6, (
            f"The worked-examples table has {len(example_rows)} numbered rows. "
            "The spec's Independent Test names six changes the document must "
            "classify without a human; a seventh is fine in prose, but the "
            "table is the part that answers them."
        )

    @pytest.mark.parametrize(
        ("marker", "expected"), _SIX_EXAMPLES, ids=lambda value: str(value)
    )
    def test_each_example_is_present_and_classified(
        self, example_rows: list[str], marker: str, expected: tuple[str, ...]
    ) -> None:
        matches = [row for row in example_rows if marker in row]
        assert len(matches) == 1, (
            f"Expected exactly one worked example mentioning {marker!r}; got "
            f"{len(matches)}. Rows were:\n" + "\n".join(example_rows)
        )
        named = _classes_in(matches[0])
        assert named, (
            f"The {marker!r} row names none of {_CLASSES}, so the reader cannot "
            f"answer the question from the table. Row was:\n{matches[0]}"
        )
        assert set(named) == set(expected), (
            f"The {marker!r} row classifies as {named}, expected {list(expected)}. "
            "Two of the six are deliberately two-valued (pre-freeze vs "
            "post-freeze); the rest must name exactly one class or they are not "
            "unambiguous."
        )

    def test_the_freeze_boundary_the_two_valued_rows_depend_on_is_explained(
        self, governance: str
    ) -> None:
        boundary = _section(governance, "### The freeze boundary")
        for phrase in ("pre-freeze", "post-freeze", "US-004"):
            assert phrase in boundary, (
                f"The freeze-boundary section does not mention {phrase!r}. Two "
                "of the six examples answer differently on either side of that "
                "boundary, so a reader who cannot tell which side they are on "
                "cannot use the table."
            )


class TestTheRecordedRulings:
    """The five rulings, each with a source a reader can go and check."""

    @pytest.mark.parametrize("marker", _RULING_MARKERS)
    def test_the_ruling_has_a_section(self, governance: str, marker: str) -> None:
        assert marker in governance, (
            f"No {marker.strip()} section in GOVERNANCE.md. The rulings are the "
            "reason this document exists rather than a link to semver.org — "
            "they are the calls this repository already made and should not "
            "re-litigate."
        )

    @pytest.mark.parametrize("marker", _RULING_MARKERS)
    def test_the_ruling_cites_a_source_file_that_exists(
        self, governance: str, marker: str
    ) -> None:
        body = _section(governance, marker.rstrip())
        assert "**Source:**" in body, (
            f"{marker.strip()} records a ruling with no `**Source:**` line. A "
            "ruling without a citation is an assertion, and the next person to "
            "disagree with it has nothing to read."
        )
        cited = [
            candidate
            for candidate in re.findall(r"`([^`]+)`", body)
            if "/" in candidate and candidate.endswith((".md", ".py"))
        ]
        existing = [
            candidate
            for candidate in cited
            if (_REPO_ROOT / candidate.split(":", 1)[0]).exists()
        ]
        assert existing, (
            f"{marker.strip()} cites no in-repo file that exists. Cited paths "
            f"were {cited}."
        )

    def test_the_413_ruling_classifies_both_the_present_and_the_fix(
        self, governance: str
    ) -> None:
        # The load-bearing half for codegen consumers: a generated client reads
        # statuses, not descriptions, so "we document a status nobody can
        # observe" and "correcting it later is a wire change" are two different
        # rulings and the document owes both.
        body = _section(governance, "### (a2) ")
        assert "no bump" in body, (
            "The (a2) ruling must say that documenting the unreachable 413 "
            "alongside the reachable 400 carries no bump — that is the ruling "
            "US-001 shipped under."
        )
        assert "MAJOR" in body, (
            "The (a2) ruling must classify the future 400 -> 413 correction. "
            "GOTCHAS records it as a wire change; the classification belongs "
            "here, or the next person weighs it themselves."
        )
        assert "codegen" in body.lower(), (
            "The (a2) ruling must say why it is load-bearing: codegen "
            "consumers read statuses, not descriptions."
        )

    def test_the_enum_ruling_carries_its_announcement_obligation(
        self, governance: str
    ) -> None:
        body = _section(governance, "### (b) ")
        assert "security-block" in body and "Release body" in body, (
            "Ruling (b) is MINOR *plus* an obligation: a security-block reason "
            "must be announced, because a consumer bucketing it as unknown is "
            "behaving correctly and silently under-reporting a refusal."
        )

    def test_the_fixture_retention_ruling_matches_the_tree(
        self, governance: str
    ) -> None:
        body = _section(governance, "### (c) ")
        golden_dir = _REPO_ROOT / "tests" / "golden"
        goldens = sorted(path.name for path in golden_dir.glob("*.json"))
        assert len(goldens) >= 2, (
            f"Ruling (c) says historical fixtures are retained, but tests/golden "
            f"holds {goldens}. If a fixture was deleted, the ruling is now false."
        )
        assert "contract_1_0_0.json" in body, (
            "Ruling (c) should name the fixture that proves it — the 1.0.0 "
            "file that stayed when 1.1.0 landed."
        )


class TestTheTwoSemverRule:
    """Image tag and contract version: independent, and mechanically mapped."""

    def test_the_independence_is_stated_with_its_worked_example(
        self, governance: str
    ) -> None:
        body = _section(governance, "## Two semvers")
        assert "v1.0.0" in body and CONTRACT_VERSION in body, (
            "The two-semver section must carry the concrete example: image "
            f"v1.0.0 serving contract {CONTRACT_VERSION}. Stated abstractly, "
            "'they are independent' reads as 'they are unrelated'."
        )
        assert "US-004" in body, (
            "The section must say which story cuts that tag. US-003 writes the "
            "rule; tagging before US-004's in-image COPY and Release assets "
            "land would ship a contract-less v1.0.0 that cannot be re-cut."
        )

    def test_the_withdrawn_tags_rule_is_cross_referenced(self, governance: str) -> None:
        body = _section(governance, "## Two semvers")
        assert "Withdrawn tags" in body and "docs/releases.md" in body, (
            "A withdrawn image tag does not withdraw a contract version, and "
            "the withdrawal rule itself lives in docs/releases.md (the "
            "v0.9.2-rc case). Cross-reference it rather than restating it."
        )

    def test_the_release_assertion_step_is_described_as_shipped(
        self, governance: str
    ) -> None:
        body = _section(governance, "## Two semvers")
        assert "contract: <version>" in body, (
            "The section must name the line the Release body carries — it is "
            "what a consumer greps for."
        )
        assert "gh release view" in body, (
            "The section must say the published body is read back and checked, "
            "not merely emitted. Emitting alone is the human-authored claim "
            "this mechanization replaced."
        )


class TestSecurityPolicy:
    """SECURITY.md: a real channel, a real support window, a linked posture."""

    def test_it_names_the_private_reporting_channel(self, security: str) -> None:
        assert (
            "https://github.com/WashingBearLabs/Forage/security/advisories/new"
            in security
        ), (
            "SECURITY.md must link GitHub private vulnerability reporting. It "
            "is the only private channel this project has — no security email, "
            "no PGP key — so the link is the policy."
        )
        assert "do not" in security.lower() and "public issue" in security.lower(), (
            "SECURITY.md must tell a reporter not to open a public issue for a "
            "suspected vulnerability, and what to do instead."
        )

    def test_it_states_a_supported_versions_policy_for_both_eras(
        self, security: str
    ) -> None:
        body = _section(security, "## Supported versions")
        lowered = body.lower()
        assert "latest release only" in lowered or "most recent release" in lowered, (
            "The pre-1.0 half of the support policy is missing: today only the "
            "newest release, release candidates included, gets fixes."
        )
        assert "latest minor of the current major" in body.lower(), (
            "The post-1.0 half is missing. A support policy that stops being "
            "true at 1.0.0 is a support policy with a three-week shelf life."
        )

    def test_the_posture_is_referenced_rather_than_duplicated(
        self, security: str
    ) -> None:
        body = _section(security, "## Deployment posture")
        assert "README.md" in body and "docs/configuration.md" in body, (
            "SECURITY.md's posture section must point at the README (the "
            "authority) and docs/configuration.md (the per-endpoint detail). A "
            "second copy of the posture paragraph is a second thing to keep "
            "true."
        )
        assert len(body.splitlines()) < 30, (
            "The posture section has grown into a duplicate of the README's "
            "note. Summarise and link; do not restate."
        )

    def test_it_repeats_the_documented_non_vulnerabilities(self, security: str) -> None:
        body = _section(security, "## Not vulnerabilities here")
        for phrase in ("private IP", "413", "authentication"):
            assert phrase in body, (
                f"The not-a-vulnerability list does not mention {phrase!r}. "
                "These are the reports this repository will receive and has "
                "already answered — ruling (d)'s IP echo, ruling (a2)'s "
                "shadowed 413, and the no-auth posture."
            )

    def test_it_points_at_governance_for_wire_changing_fixes(
        self, security: str
    ) -> None:
        assert "contract/GOVERNANCE.md" in security, (
            "A security fix that moves the wire follows GOVERNANCE's expedited "
            "path — a MINOR with a compatibility window, or an immediate "
            "MAJOR. SECURITY.md must send the reader there rather than "
            "inventing a second policy."
        )


class TestPullRequestTemplate:
    """The checklist: the bump questions, and the invariants that bite authors."""

    def test_it_asks_the_four_bump_questions(self, pr_template: str) -> None:
        body = _section(pr_template, "## Contract")
        questions = (
            "wire bytes",
            "Which class",
            "Regenerated the contract",
            "GOVERNANCE",
        )
        for phrase in questions:
            assert phrase in body, (
                f"The contract checklist is missing {phrase!r}. The four "
                "questions are: does this change wire bytes, which class is "
                "it, did you regenerate, did you consult GOVERNANCE."
            )

    def test_it_names_the_regeneration_command_exactly(self, pr_template: str) -> None:
        assert REGEN_COMMAND in pr_template, (
            f"The PR template must name {REGEN_COMMAND!r} verbatim — it is the "
            "command the checklist item is asking whether you ran."
        )

    def test_it_carries_the_invariants_that_bite_pr_authors(
        self, pr_template: str
    ) -> None:
        body = _section(pr_template, "## Standing invariants")
        for phrase in ("ARG", "sanitizer_revision", "poppy", "logged"):
            assert phrase in body, (
                f"The standing-invariants checklist is missing {phrase!r}. "
                "These are the six in CLAUDE.md, reduced to the ones a PR "
                "author trips over."
            )

    def test_the_hashed_source_count_matches_the_code(self, pr_template: str) -> None:
        # The template tells an author how many files rotate the revision. That
        # number lives in pipeline/sanitizer_revision.py and has moved before.
        expected = _NUMBER_WORDS[len(_REVISION_SOURCES)]
        # Flowed, because the template wraps at 92 columns and a phrase that
        # happens to straddle a line break is still the phrase.
        flowed = " ".join(pr_template.split())
        assert f"{expected} hashed" in flowed, (
            f"The PR template must say '{expected} hashed' — "
            f"_REVISION_SOURCES currently holds {len(_REVISION_SOURCES)} files, "
            "and an author counting on a stale number takes a rotation they "
            "did not mean to take."
        )

    def test_it_lists_exactly_the_checks_the_workflow_requires(
        self, pr_template: str
    ) -> None:
        # The six contexts registered as required on `main` are exactly the
        # jobs `publish` hangs off, and the template tells contributors so. A
        # seventh gate that never reaches this document leaves it telling a
        # shorter truth than the workflow enforces.
        #
        # Read out of *that sentence*, not out of the whole file: the mutation
        # run caught the looser version passing while the sentence was a check
        # short, because `secret-grep` is also named in the invariants section.
        # Checking a document's vocabulary is not checking its claim — the same
        # lesson the publish job's `if`-block assertions are written around.
        workflow: Any = yaml.safe_load(CI_WORKFLOW_PATH.read_text())
        needs: Any = workflow["jobs"]["publish"].get("needs", [])
        gates = [needs] if isinstance(needs, str) else [str(name) for name in needs]
        flowed = " ".join(pr_template.split())
        sentence = re.search(r"The (\w+) checks required on `main` are (.+?) —", flowed)
        assert sentence is not None, (
            "The PR template must carry a sentence of the form 'The <n> checks "
            "required on `main` are `a`, `b`, ... — ...'. It is the only place "
            "a contributor learns what has to be green."
        )
        listed = re.findall(r"`([^`]+)`", sentence.group(2))
        assert sorted(listed) == sorted(gates), (
            f"The PR template's required-checks sentence names {sorted(listed)}; "
            f"the workflow hangs `publish` off {sorted(gates)}. Those are the "
            "contexts required on `main`, so a contributor reading a shorter "
            "list plans a shorter round trip."
        )
        assert sentence.group(1) == _NUMBER_WORDS[len(gates)], (
            f"The sentence says {sentence.group(1)!r} checks and lists {len(gates)}."
        )
