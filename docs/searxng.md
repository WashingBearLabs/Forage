# The `forage-searxng` companion image

`ghcr.io/washingbearlabs/forage-searxng` is upstream's SearXNG at a pinned
digest plus the baked configuration in `searxng/config/`. Nothing is forked, no
entrypoint is wrapped, and the whole Dockerfile is a `FROM` and a `COPY`.

Forage's search stage is a JSON API client: `pipeline/orchestrator.py` calls
`GET /search?format=json&engines=…`. This image exists so that call works out
of the box, on defaults that are safe to publish. Written by
`forage-ci-and-image` US-004; the service image's own lane is in
`docs/releases.md`.

> **Public since US-008's 2026-09-10 flip** — the repository and both packages;
> anonymous pulls verified at the gate.

## Running it

```bash
docker run -d --name searxng \
  -e SEARXNG_SECRET="$(head -c 32 /dev/urandom | base64)" \
  -p 8080:8080 \
  ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc
```

`SEARXNG_SECRET` is **required**. There is no default and no baked literal, and
an unset variable is a hard start failure — see below. Nothing else is needed:
no bind mount, no config file, no `settings.yml` of your own.

| Variable | Required | What it does |
|---|---|---|
| `SEARXNG_SECRET` | **yes** | Signs the HTML UI's session cookies. Any long random string. |
| `SEARXNG_VALKEY_URL` | no | `valkey://host:6379/0`. Only meaningful together with the next one. |
| `SEARXNG_LIMITER` | no | `true` turns the rate limiter on. **Read the limiter section before you do.** |

## `SEARXNG_SECRET`: required, and loudly

Verified against the pinned digest, both ways:

* **set** — the instance starts and serves.
* **unset** — the container exits **1** during startup, having logged
  `ERROR:searx.webapp: server.secret_key is not changed. Please use something
  else instead of ultrasecretkey.`

The mechanism is worth knowing, because it is not the one you would guess.
Upstream declares `server.secret_key` with no default and the environment name
`SEARXNG_SECRET` (`searx/settings_defaults.py`). The baked config deliberately
omits the key, so with `use_default_settings: true` the value falls through to
upstream's own placeholder — and `searx/webapp.py` refuses to serve with it.
The image's entrypoint does substitute a random secret into a *generated*
settings file, but only when none exists; a baked file means that path never
runs.

The config this image replaced baked the literal `poppy-searxng-internal`. A
known secret shipped in a published image is not a secret: it signs sessions for
every instance that ever pulled it. Failing to start is the honest alternative,
and `tests/test_searxng_docker.py` fails if a literal comes back.

## Rate limiting: off, and why that is the honest setting

**`server.limiter` is `false` in the baked config.** The reasoning is measured
rather than assumed, and it is the opposite of where this story started.

The plan was `limiter: true` with the Valkey requirement documented, because
`limiter: true` with no backend is inert theatre — upstream logs
`The limiter requires Valkey` at ERROR and serves every request unthrottled.
That half is true and the CI smoke reproduces it.

The other half does not survive contact with this image's only client.
Measured against the pinned digest with a real Valkey attached:

| Client | Result |
|---|---|
| Forage's httpx client | **429 on the first request** — `http_accept_language` blocks any request without an `Accept-Language` header, and httpx sends none |
| `curl` | **429 on the first request** — `http_accept_encoding`, and again `http_user_agent`, whose regex matches curl, wget and python-requests |
| a perfectly browser-shaped client | 200, 200, 200, **429** — `ip_limit.API_MAX = 4` requests per `API_WINDOW = 3600 s`, per client network, for any `format != html` |

Neither `API_MAX` nor `API_WINDOW` is settable from `limiter.toml`; they are
module constants in `searx/botdetection/ip_limit.py`. So a working limiter on
this image does not rate-limit abuse of a search API — it refuses the search
API. `limiter: true` here would be an outage switch labelled as protection, and
the outage would be *intermittent*: four searches an hour succeed.

What protects this image is not being exposed. It is a sidecar on a private
network with one client. `docs/configuration.md` and spec 4's compose fragments
are where that placement is described.

### Turning it on anyway

A deployment that must expose the instance can opt in with no rebuild:

```bash
docker run -d \
  -e SEARXNG_SECRET=… \
  -e SEARXNG_LIMITER=true \
  -e SEARXNG_VALKEY_URL=valkey://valkey:6379/0 \
  ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc
```

**Both halves are required.** `SEARXNG_LIMITER=true` alone gets you the inert
limiter and the ERROR line. And an API consumer then needs a `pass_ip` entry
for its own network in an overlay, because the numbers in the table above still
apply — which is an IP-trust relaxation, scoped to one network, made
deliberately by the operator who needs it, rather than baked into an image
everybody pulls.

### The environment variable name, verified

**`SEARXNG_VALKEY_URL`.** This was the one name in the plan that nothing in
either repository could confirm, and the guess was wrong-ish:

* `SEARXNG_VALKEY_URL` maps to `valkey.url` and is the current name.
* `SEARXNG_REDIS_URL` maps to `redis.url`, which
  `searx/settings_defaults.py` marks `# redis is deprecated ..`. It still
  works on this pin — `searx/valkeydb.py` prefers `valkey.url` and falls back
  to `redis.url` — but it emits `DeprecationWarning: setting redis.url is
  deprecated, use valkey.url`. Verified live: both names initialise the
  limiter.

Use the Valkey name. The Redis one is recorded here only so a reader who finds
it in an older document knows it is superseded rather than wrong.

## No IP-trust relaxations

The baked config removes three things and adds none.

**No `pass_ip` wildcard.** The previous config carried an all-addresses pass
list, which gives every address on the internet unrestricted access — the
`ip_lists` method has priority over every other botdetection method, so that
single line disables bot detection entirely while `limiter:` still reads as
though it were on. Both lists are now explicitly empty.

**No client-header trust.** The previous config set `forwarded_for_header` and
`real_ip_header`. Once this image is reachable directly, those are
client-spoofable: a caller who can set `X-Forwarded-For` picks its own
rate-limit identity. As of the pinned release they are not even schema keys any
more — header trust moved to `botdetection.trusted_proxies` in `limiter.toml`,
whose default is loopback only (`127.0.0.0/8`, `::1`). That default is correct
here: a peer on a container network is not loopback, so its headers are ignored
and the connecting address is the client. **Nothing is added to
`trusted_proxies`**; a deployment behind a reverse proxy adds that proxy's
network in its own overlay, where it is true.

**No `pass_searxng_org`.** Upstream defaults this to `true`, which grants a
hardcoded set of SearXNG organisation IPs unrestricted access. Reasonable for a
public instance that wants `check.searx.space` to monitor it; an unasked-for
relaxation in a sidecar nobody monitors.

**`link_token` stays off.** Measured on the pinned digest, limiter installed,
browser-shaped JSON client: `200 200 200 200 429` with it off (`ip_limit.API_MAX = 4`), `200 200 429 302`
with it on (`BURST_MAX_SUSPICIOUS = 2`, then `SUSPICIOUS_IP_MAX`). It strictly
narrows an already-narrow API budget, and never gets the chance to matter for
Forage's real client, which the header methods refuse two steps earlier.

`tests/test_searxng_docker.py` asserts every one of these as a negative,
because a removal leaves nothing behind to notice at review.

## The Poppy overlay pattern

Poppy's deployment needs relaxations this image must not ship. The mechanism is
a mounted overlay, not a different image:

```yaml
services:
  searxng:
    image: ghcr.io/washingbearlabs/forage-searxng:0.1.1-rc
    environment:
      SEARXNG_SECRET: ${SEARXNG_SECRET:?set me}
    volumes:
      - ./searxng-overlay/limiter.toml:/etc/searxng/limiter.toml:ro
```

`/etc/searxng/settings.yml` and `/etc/searxng/limiter.toml` are ordinary files
in the image, so a bind mount over either replaces it wholesale — an overlay is
a *replacement* of that file, not a merge, and it must therefore repeat
whatever it still wants from the baked one. The `use_default_settings: true`
overlay relationship is with *upstream's* settings, not with this image's.

The spec-6 work that wires Poppy to this image owns the overlay's contents. Two
things to carry into it: Poppy's instance sits behind Traefik on `poppy-net`, so
if it turns the limiter on it needs both that network in `trusted_proxies` and
its Forage container's network in `pass_ip`; and the `poppy-searxng-internal`
secret currently in Poppy's compose is rotated there.

## Engines

The baked config names the engines Forage cares about. Be precise about what
that buys: with `use_default_settings: true` the `engines:` list **merges into**
upstream's — it overrides the named entries and inherits everything else, so
the running image enables the full upstream default set (~84 engines at this
pin, visible at `/config`), not just the four below. Naming an engine is what
lets this config *hold its state* against upstream changes; it does not shrink
the set. The reason that's acceptable is the client: Forage names its engines
on every query (`engines=` parameter), so the ~80 inherited engines are
reachable only by a caller who asks for them.

| Engine | State |
|---|---|
| `duckduckgo`, `brave`, `startpage`, `mojeek` | enabled, 5 s timeout each |
| `bing` | listed and **disabled** — blocks homelab and cloud IP ranges aggressively |
| `ahmia` | listed and **disabled** — a Tor hidden-service index, not general web search |

With `use_default_settings: true`, an entry is what stops an upstream release
re-enabling something. Upstream keeps adding engines this repository has never
vetted (observed live 2026-08-19: `aol`, `karmasearch videos`), and
`pipeline/orchestrator.py` names its engines on every query precisely so a new
default cannot change what a Forage search fans out to.

The *named-enabled* set and `_SEARXNG_ENGINES` in `pipeline/orchestrator.py`
are a contract with two ends, and `tests/test_searxng_docker.py` asserts they
are the same set — the parity is over the entries this file declares, not over
everything the merged config enables. An engine Forage asks for that is
disabled here is a request answered with nothing, and it surfaces as thin
results rather than as an error.

Two names were **removed** rather than left disabled: `torch` and
`karmasearch` no longer exist upstream, and naming a missing engine is not a
no-op — SearXNG logs `Cannot load engine` with a traceback at startup for each
one, disabled or not.

## Bumping the pin

This replaces Poppy's old habit of pulling `searxng/searxng:latest` at deploy
time. That habit meant the configuration a smoke test proved and the image an
operator ran were different builds, and nobody could say which SearXNG was in
production.

1. Resolve the new digest and read what it is:
   ```bash
   docker pull searxng/searxng:latest
   docker image inspect searxng/searxng:latest \
     --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
   docker image inspect searxng/searxng:latest --format '{{json .RepoDigests}}'
   ```
2. Update `searxng/Dockerfile` — the digest on the `FROM` line, and the release
   and date on the comment lines above it. A guard test fails if the comment
   stops recording a release, because a bare 64-hex string cannot be
   maintained.
3. Smoke it locally, exactly as CI does:
   ```bash
   docker build -t forage-searxng:ci searxng/
   uv run python searxng_smoke.py --image forage-searxng:ci
   uv run python searxng_smoke.py --image forage-searxng:ci --live
   ```
4. `uv run pytest -q tests/test_searxng_docker.py` — the config-regression and
   engine-parity guards.
5. Open a PR. `searxng-build` and `searxng-smoke` run on it.
6. Tag `searxng-v<version>` on `main` once merged. `searxng-publish` runs after
   `test`, `searxng-build` and `searxng-smoke`.

**Engine rot is now visible earlier, and this is worth being explicit about
because it is a trade rather than a pure win.** A moving `:latest` picked up
upstream's engine fixes silently; a pinned digest does not. What replaces that
is two signals: the advisory live-engine probe fails at bump time (a red
advisory line in `searxng-smoke`, deliberately non-blocking), and Poppy's
runtime web probes notice in production. Neither is automatic repair. If an
engine breaks, someone bumps the pin.

`.github/workflows/ci.yml` does not automate this. Renovate/dependabot for the
SearXNG pin is declared out of scope by the spec.

## The tag scheme

| You push | The registry gets |
|---|---|
| `searxng-v0.1.0` | `0.1.0`, `0.1`, `latest` |
| `searxng-v0.2.0-rc` | `0.2.0-rc` only |
| a commit on `main` | nothing |
| `v*` (the service lane) | nothing on this image |

The version is the tag with `searxng-v` stripped off. There is no
`main`-push publish: unlike the service image, this one changes only when the
pinned digest or the baked config changes, so a `sha-` tag per commit would be
a registry full of identical images.

**One trap, recorded because it nearly shipped.** The service lane tests for a
pre-release with `!contains(github.ref, '-')`. Copied here, that is *always
false* — every ref in this lane contains a hyphen, in the `searxng-v` prefix
itself — so `latest` would never move for any release. The companion lane's
policy is written against the stripped version instead, and
`tests/test_ci_workflow.py` evaluates it for both a release and a pre-release.

### Cross-fire

`refs/tags/v` and `refs/tags/searxng-v` are mutually exclusive prefixes, and
every job in both lanes says so in its own `if:`. A `searxng-v*` tag skips
`build-amd64`, `secret-grep`, `smoke` and `publish`; a `v*` tag skips all three
companion jobs. The tests evaluate each job's condition against each tag shape
rather than reading it, in both directions.

## What CI proves, and what it does not

`searxng-smoke` runs `searxng_smoke.py`, which stands the image up beside a
Valkey **on a Docker network created with `--internal`** — no egress at all, so
no engine on the internet is contacted. That is what lets this job sit in a
publish gate chain: a live third-party query there would reproduce the
every-engine-throttled outage `kit_tools/docs/GOTCHAS.md` records, with a
release as the victim.

Four blocking phases:

1. `SEARXNG_SECRET` unset ⇒ the container exits non-zero, naming the secret.
2. Shipped defaults ⇒ `format=json` answers **200** with a parseable envelope
   carrying `results`, and no bot-detection block page.
3. Six consecutive JSON requests all answer 200 — past `API_MAX`, which is the
   guard that notices if someone turns the limiter back on.
4. `SEARXNG_LIMITER=true` **with** Valkey ⇒ the limiter installs for real (it
   writes to the Valkey DB, asserted by reading that DB's key count, and it
   refuses an API-shaped client); **without** Valkey ⇒ the inert-limiter error
   is logged and every request is served. The differential is what makes "the
   limiter initialised" a measurement rather than an assertion.

The advisory live-engine probe runs afterwards on an ordinary network,
`continue-on-error: true`, with its outcome written to the job summary.

Two things CI does **not** prove:

* **arm64 is ungated.** The image is published for `linux/amd64` and
  `linux/arm64`, but nothing in this workflow has ever executed the emulated
  leg. It is built from the same commit and the same digest-pinned multi-arch
  base, and that is the whole of the claim. A consumer on arm64 is the first
  thing to run that image.
* **The engines are not guaranteed.** Whether DuckDuckGo, Brave, Startpage and
  Mojeek answer from any given IP is not this repository's to promise. That is
  why the live probe is advisory.

## Reproducing a failure

The smoke is a committed script, not a heredoc, so a red job reproduces in two
commands:

```bash
docker build -t forage-searxng:ci searxng/
uv run python searxng_smoke.py --image forage-searxng:ci        # blocking phases
uv run python searxng_smoke.py --image forage-searxng:ci --live # advisory
uv run python searxng_smoke.py --image forage-searxng:ci --keep # leave containers up
```

`--keep` leaves the containers and the network behind; note that Docker
silently ignores `-p` on an `--internal` network, so inspect from a container
on that network rather than from the host:

```bash
docker run --rm --network forage-searxng-smoke curlimages/curl:latest \
  -s 'http://forage-searxng-smoke-sx:8080/search?q=hello&format=json'
```
