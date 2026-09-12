# Security policy

Forage fetches attacker-influenced content from the internet for a living, and it ships
with **no authentication**. That combination is why this file exists. Written by
`feature-forage-contract` US-003.

## Reporting a vulnerability

**Use GitHub private vulnerability reporting:**
[github.com/WashingBearLabs/Forage/security/advisories/new](https://github.com/WashingBearLabs/Forage/security/advisories/new)
(*Security → Advisories → Report a vulnerability*).

It is enabled on this repository — verified 2026-09-11 — and it is the **only** private
channel: this project publishes no security email, and there is no PGP key. The report,
the discussion and the fix stay private until an advisory is published.

**Please do not** open a public issue or pull request for a suspected vulnerability. If
private reporting is somehow unavailable to you, open an issue saying only that you have a
security report and asking for a private channel — no detail, no proof of concept.

A useful report carries: the Forage version (image tag, or `/health`'s `contract_version`
plus `sanitizer_revision`), how Forage was deployed and what could reach it, the request
that triggers it, what you expected and what happened.

**What to expect.** This is a small project maintained by one person. Reports are handled
on a best-effort basis with no SLA; expect an acknowledgement within about a week. You will
be credited in the advisory unless you ask not to be.

## Supported versions

| Version | Supported |
|---|---|
| The most recent release | **yes** — fixes ship as a new tag |
| Anything older | no — no backports, no patch branches |

Pre-1.0 (where the project is today) that means **the latest release only**, release
candidates included. From `1.0.0` onward it means **the latest minor of the current major**:
a fix lands on the newest minor and the upgrade path is forward, not sideways.

Release tags, the pre-release policy and what a green publish does and does not prove are
in [`docs/releases.md`](docs/releases.md).

The **contract version is separate** from the image tag and may not move at all for a
security fix — see [`contract/GOVERNANCE.md`](contract/GOVERNANCE.md). A fix that must
change the wire follows that document's expedited path: a MINOR with a stated compatibility
window, or an immediate MAJOR if it cannot be made compatible. **Never a silent break.**

## Deployment posture

Summarised in one line so a reporter knows the intended threat model, and deliberately not
duplicated: the authority is the README.

**No authentication, private network only, and a server-side-request-forgery target by
nature** — making outbound requests on request is Forage's job. Network placement is the
operator's first control, not Forage's. Read
[the README's *Deployment posture* note](README.md#deployment-posture--read-before-you-run-it)
before running it, and [`docs/configuration.md`](docs/configuration.md)'s posture tables for
the per-endpoint detail, including the unauthenticated `/docs`, `/redoc` and `/openapi.json`.

Forage is also **not a trust boundary**. It reports signals and confidence; the calling
agent's own security layer owns every trust decision. A report that a downstream agent
trusted Forage's output belongs to that agent. A report that Forage's *signal was wrong* —
that content it called clean was not — belongs here.

## In scope

- Reaching a private, loopback, link-local or otherwise reserved address through
  `/retrieve`, `/search` or `/extract`: a validator bypass, a DNS-rebinding race, a redirect
  chain that escapes the audit.
- A sanitization bypass: injected content that survives the pipeline while the response
  reports it as clean and `promptguard_loaded` is `true`.
- Code execution, path traversal or sandbox escape in extraction — HTML, PDF or upload
  parsing reached by a crafted document.
- A credential disclosed anywhere: in a log line (`VALKEY_URL` may carry a password), in a
  response body, or baked into a published image layer.
- Cache poisoning: making one caller's request return another's content, or content keyed
  under a policy it was not sanitized with.
- Anything in the published image that the gate chain was supposed to stop — a baked
  secret, a layer that is not the one CI gated.

## Not vulnerabilities here

These are documented properties. Reporting them is welcome as a *discussion*, but they are
known and will be closed as such.

- **No authentication on any endpoint**, `/docs`, `/redoc` and `/openapi.json` included.
  That is the posture, stated on the README's first screen.
- **Forage fetches the URL you ask it to fetch**, including one that makes it a request
  proxy for whoever can reach the port. Anyone who can reach an unauthenticated Forage can
  do that by design; the control is network placement.
- **A `/retrieve` refusal echoes the resolved private IP** in its `reason`
  (`URL '<url>' resolves to private IP <ip>`), so a caller can use Forage as a DNS oracle
  for the network it sits on. Under the stated posture the caller already reaches that
  network. It is a documented caveat for anyone who breaks the posture, and redacting it is
  a wire change — [`contract/GOVERNANCE.md`](contract/GOVERNANCE.md), ruling (d).
- **An over-sized upload to `/extract` returns 400, not the 413 the size middleware
  emits.** A known wrong-status bug with the byte cap intact — nothing over the limit is
  spooled or extracted. Measurements in `kit_tools/docs/GOTCHAS.md`; fixing it is a contract
  change, ruling (a2).
- **Resource exhaustion by a caller who is allowed to call.** Rate limiting and request
  admission exist (`docs/configuration.md`), but an unauthenticated service on a private
  network does not pretend to survive a determined insider.
- **The companion `forage-searxng` image ships with rate limiting off.** Deliberate,
  measured, and documented in [`docs/searxng.md`](docs/searxng.md) — a working SearXNG
  limiter refuses the JSON API the image exists to serve. It is built for an internal
  network, like the official Redis and Postgres images.
- **Model weights are not shipped.** Llama Prompt Guard 2 is fetched at runtime; a weights
  problem is upstream's, and a weights-free Forage reports itself `degraded` rather than
  pretending content was scanned.

## How a fix ships

A fix lands on `main` through a pull request behind the same six gates as anything else
(lint, typecheck, test, build-amd64, secret-grep, smoke), then a new tag publishes a
verified image and a Release. The advisory is published alongside it, and if the fix moves
the wire, the Release body says so and `contract/GOVERNANCE.md`'s expedited path applies.
