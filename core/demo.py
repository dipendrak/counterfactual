"""python -m core.demo [model.json] [delta.json] — full pipeline on a fixture, prints SimResult."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core.model import CashflowModel, DecisionDelta
from core.run import run

FIXTURES = Path(__file__).parent / "fixtures"


def main(argv: list[str]) -> None:
    model_path = Path(argv[0]) if len(argv) > 0 else FIXTURES / "model_fixture.json"
    delta_path = Path(argv[1]) if len(argv) > 1 else FIXTURES / "delta_fixture.json"
    model = CashflowModel.from_dict(json.loads(model_path.read_text()))
    delta = DecisionDelta.from_dict(json.loads(delta_path.read_text()))

    result = run(model, delta).to_dict()
    # Paths are ~400KB; print the summary plus the first path so the terminal stays readable.
    n_paths = len(result["paths"])
    result["paths"] = result["paths"][:1] + [f"... {n_paths - 1} more paths elided"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
