"""AgentDojo sampler (MIT; ``ethz-spylab/agentdojo``) — spec 2 US-004.

``--input`` is a checkout of the repository at the pinned commit. The sampler
reads the injection tasks' ``GOAL`` strings from every
``default_suites/<version>/<suite>/injection_tasks.py`` *statically* (``ast``;
nothing upstream is imported or executed) and wraps each goal in the
repository's own ``important_instructions`` attack template, which is how the
benchmark delivers it inside a tool output. Each distinct injected tool output
is one row; it is re-rendered as an article ``page`` or a ``search`` snippet —
never committed as the tool-environment injection it came from.

    uv run python -m scripts.corpus.ingest.agentdojo \\
        --input ~/.cache/forage-corpus-inputs/agentdojo --revision <sha>
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from scripts.corpus.ingest import render
from scripts.corpus.ingest.common import IngestError, Row, Source, run, shuffled

_TEMPLATE_FILE: Final[str] = "important_instructions_attacks.py"
_DEFAULTS_FILE: Final[str] = "base_attacks.py"


def _value(node: ast.expr, scopes: Sequence[dict[str, str]]) -> str | None:
    """A string expression built only from literals and known string names."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        for scope in scopes:
            if node.id in scope:
                return scope[node.id]
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _value(node.left, scopes), _value(node.right, scopes)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                if part.format_spec is not None or part.conversion != -1:
                    return None
                resolved = _value(part.value, scopes)
            else:
                resolved = _value(part, scopes)
            if resolved is None:
                return None
            parts.append(resolved)
        return "".join(parts)
    return None


def _assignments(
    body: Sequence[ast.stmt], scopes: Sequence[dict[str, str]]
) -> dict[str, str]:
    """Name -> string for every simple string assignment in ``body``, in order."""
    scope: dict[str, str] = {}
    for statement in body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target, value = statement.target, statement.value
        else:
            continue
        if isinstance(target, ast.Name):
            resolved = _value(value, [scope, *scopes])
            if resolved is not None:
                scope[target.id] = resolved
    return scope


def _module_scope(path: Path) -> tuple[ast.Module, dict[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return tree, _assignments(tree.body, [])


def _find(root: Path, name: str) -> Path:
    matches = sorted(root.rglob(f"attacks/{name}"))
    if not matches:
        raise IngestError("input_malformed")
    return matches[0]


def _template(root: Path) -> str:
    """The ``important_instructions`` string with the default user / model names."""
    tree, _ = _module_scope(_find(root, _TEMPLATE_FILE))
    _, defaults = _module_scope(_find(root, _DEFAULTS_FILE))
    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "ImportantInstructionsAttack"
        ):
            template = _assignments(node.body, []).get("_JB_STRING")
            user = defaults.get("DEFAULT_USER_NAME")
            model = defaults.get("DEFAULT_MODEL_NAME")
            if template is None or user is None or model is None:
                break
            return template.replace("{user}", user).replace("{model}", model).strip()
    raise IngestError("input_malformed")


def read_rows(root: Path) -> tuple[list[Row], Counter[str], int]:
    """One row per distinct injected tool output; counts of what was set aside."""
    if not root.is_dir():
        raise IngestError("input_malformed")
    template = _template(root)
    skipped: Counter[str] = Counter()
    rows: list[Row] = []
    seen: set[str] = set()
    read = 0
    for path in sorted(root.rglob("default_suites/*/*/injection_tasks.py")):
        version, suite = path.parent.parent.name, path.parent.name
        tree, module = _module_scope(path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            has_goal = any(
                isinstance(s, ast.Assign | ast.AnnAssign)
                and any(
                    isinstance(t, ast.Name) and t.id == "GOAL"
                    for t in (s.targets if isinstance(s, ast.Assign) else [s.target])
                )
                for s in node.body
            )
            if not has_goal:
                continue
            read += 1
            goal = _assignments(node.body, [module]).get("GOAL")
            if goal is None:
                skipped["unresolved_goal"] += 1
                continue
            if goal in seen:
                skipped["duplicate"] += 1
                continue
            seen.add(goal)
            text = template.replace("{goal}", goal)
            rows.append(
                Row(ref=f"{version}/{suite}/{node.name}", text=text, marker_text=goal)
            )
    if read == 0:
        raise IngestError("input_malformed")
    return rows, skipped, read


def _render(
    row: Row, text: str, subject: str, number: int
) -> tuple[str, dict[str, str]]:
    del row, subject
    if number % 2 and len(text) <= 2_000:
        url = f"https://reviews.example.org/agentdojo/{number}"
        return "search", render.search_snippet(url, text)
    url = f"https://notes.example.com/agentdojo/{number}"
    return "page", render.article_page(url, text)


def sample(rows: list[Row], seed: int, limit: int) -> list[tuple[Sequence[Row], int]]:
    """One stratum: every row in a seed-fixed order, capped at ``limit``."""
    return [(shuffled(rows, seed), limit)]


SOURCE: Final[Source] = Source(
    name="AgentDojo",
    url="https://github.com/ethz-spylab/agentdojo",
    licence="MIT",
    framing="indirect",
    shape="tool output",
    default_limit=40,
    render=_render,
)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    return run(
        SOURCE,
        read_rows,
        sample,
        argv,
        prog="python -m scripts.corpus.ingest.agentdojo",
        description=(
            "Sample AgentDojo injection tasks from a local checkout into "
            "re-rendered corpus records (ids and counts are printed, never text)."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
