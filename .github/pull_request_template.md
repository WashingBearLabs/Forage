<!--
Delete any section that genuinely does not apply, but delete it deliberately: every
line below is here because forgetting it has cost this repository something.
-->

## What and why

<!-- One paragraph. What changes, and what it is for. -->

## Contract

`main` is PR-only and the wire contract is frozen (`contract/openapi.yaml`, version in
`pipeline/contract.py`). Answer all four — "no" is a fine answer, an unanswered one is not.

- [ ] **Does this change wire bytes?** (a response field, a status code, an enum member's
      meaning, a request field's requiredness). The parity tests in
      `tests/test_contract_errors.py` / `tests/test_contract_metrics.py` answer this for you.
- [ ] **Which class is it?** MAJOR / MINOR / PATCH / no bump, per `contract/GOVERNANCE.md`'s
      classification table — and `CONTRACT_VERSION` bumped if it is not "no bump".
- [ ] **Regenerated the contract?** Anything that moves the document — a response model, a
      `responses=` declaration, a field description, a FastAPI bump — needs
      `uv run python -m scripts.export_contract`, which writes **three** files that must be
      committed together. `tests/test_contract_export.py` is red until you do.
- [ ] **Golden fixture handled?** A wire change adds a *new* `tests/golden/contract_X_Y_Z.json`;
      a documentation-only change regenerates the current one. Older fixtures are never
      edited or deleted (GOVERNANCE ruling (c)).
- [ ] **GOVERNANCE consulted** for anything that smells like a break, an enum addition, or an
      urgent security tightening (which is an expedited MINOR *with a compatibility window* —
      never a silent break).

## Standing invariants

The ones that bite PR authors here, in the order they bite. `CLAUDE.md` carries all six.

- [ ] **No `ARG` in the `Dockerfile`.** The build takes no arguments at all — a build arg is
      not a secret, `docker history` reads it back out of any registry the image reaches.
      Pass secrets at runtime. `tests/test_dockerfile.py` and CI's `secret-grep` both refuse.
- [ ] **`sanitizer_revision` rotation is deliberate or it is a bug.** Editing any of the eight
      hashed `pipeline/` files (`contract.py`, the stage modules, `orchestrator.py`) rotates
      the revision and flushes every cached sanitization keyed on it. Take that at a boundary,
      on purpose, with the before/after recorded in `docs/bootstrap-notes.md` — never as a
      drive-by inside a behavioural change, where it is indistinguishable from a real
      sanitizer change.
- [ ] **Nothing imports `poppy`**, and no new name contains "poppy".
- [ ] **Degradation stays loud.** `/health` returns 200 with an honest body; a missing model,
      an unreachable cache or a refused fetch never looks healthy.
- [ ] **No credential-bearing value is logged** (`VALKEY_URL` may carry a password; `cache.py`
      keeps a closed log vocabulary).
- [ ] **Docs moved with the code**: `docs/configuration.md` for any new env var or `config.yaml`
      key, `kit_tools/docs/GOTCHAS.md` for any landmine found, `kit_tools/testing/TESTING_GUIDE.md`
      for new test modules and counts.

## Gates

Green locally before pushing — a red gate costs a round trip on the free Actions tier:

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright
```

- [ ] All four clean, and tests ship in the same commit as the code they cover.

The six checks required on `main` are `lint`, `typecheck`, `test`, `build-amd64`,
`secret-grep` and `smoke` — the same set `publish` hangs off, so nothing reaches
`ghcr.io/washingbearlabs/forage` over a red gate.
