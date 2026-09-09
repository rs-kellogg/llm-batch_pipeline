from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import atomic_write_json, utc_now


def load_state(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "state.json"
    if not path.exists():
        raise FileNotFoundError(f"Run state not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        previous = run_dir / "state.previous.json"
        if previous.exists():
            return json.loads(previous.read_text(encoding="utf-8"))
        raise


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(run_dir / "state.json", state)


def resolve_run(run: str | Path) -> Path:
    candidate = Path(run).expanduser()
    if candidate.exists():
        return candidate.resolve()
    local = Path("runs") / candidate
    if local.exists():
        return local.resolve()
    raise FileNotFoundError(f"Run not found: {run}. Pass the run directory printed by 'prepare'.")

