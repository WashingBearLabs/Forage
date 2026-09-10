"""Operator scripts that are *not* part of the running service.

Nothing here is copied into the image — the Dockerfile's ``COPY`` list is
filename-enumerated (``CLAUDE.md`` invariant 3) and names none of it. These
modules are run by hand, from a checkout, through ``uv run``.

It is a package rather than a bag of loose files for one reason: ``python -m
scripts.<name>`` makes the repository root the import root, so a script can
``import model_fetcher`` exactly the way ``contract_smoke.py`` does from the
top level, with no ``sys.path`` insertion (``kit_tools/docs/CONVENTIONS.md``
forbids those) and no dependence on the project happening to be installed.
"""
