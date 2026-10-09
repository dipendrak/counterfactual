from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from core.demo import main as demo_main
from core.model import (
    CLAIM_KINDS,
    CLAIM_UNITS,
    CashflowModel,
    Claim,
    DecisionDelta,
    SimResult,
)
from core.run import run

CORE = Path(__file__).parent.parent


def test_cashflow_model_round_trip(model_dict):
    m = CashflowModel.from_dict(model_dict)
    assert CashflowModel.from_dict(m.to_dict()) == m


def test_decision_delta_round_trip(delta_dict):
    d = DecisionDelta.from_dict(delta_dict)
    assert DecisionDelta.from_dict(d.to_dict()) == d


def test_sim_result_json_round_trip(model, delta):
    r = run(model, delta, n_paths=50)
    restored = SimResult.from_dict(json.loads(json.dumps(r.to_dict())))
    assert restored == r


def test_recurring_delta_requires_cadence():
    with pytest.raises(ValueError):
        DecisionDelta(kind="recurring", amount=10.0, start_day=0)


def test_unknown_delta_kind_rejected():
    with pytest.raises(ValueError):
        DecisionDelta(kind="sometimes", amount=10.0, start_day=0)


def test_zero_cadence_obligation_rejected(model_dict):
    model_dict["recurring"][0]["cadence_days"] = 0
    with pytest.raises(ValueError):
        CashflowModel.from_dict(model_dict)


def test_duplicate_obligation_ids_rejected(model_dict):
    model_dict["recurring"][1]["obligation_id"] = model_dict["recurring"][0]["obligation_id"]
    with pytest.raises(ValueError):
        CashflowModel.from_dict(model_dict)


def test_claim_rejects_unknown_kind():
    with pytest.raises(ValueError):
        Claim(claim_id="c1", kind="vibes", value=1.0, unit="count", evidence=["t"])


def test_every_claim_has_evidence(model, delta):
    claims = run(model, delta).claims
    assert claims
    assert all(c.evidence for c in claims)


def test_claim_evidence_is_real_model_txn_ids(model, delta):
    known = set(model.all_evidence())
    for c in run(model, delta).claims:
        assert set(c.evidence) <= known


def test_claim_kinds_and_units_in_vocab(model, delta):
    for c in run(model, delta).claims:
        assert c.kind in CLAIM_KINDS
        assert c.unit in CLAIM_UNITS or (c.kind == "culprit_label" and c.unit is None)


def test_full_claim_set_emitted_on_fixture(model, delta):
    kinds = [c.kind for c in run(model, delta).claims]
    assert sorted(kinds) == sorted(CLAIM_KINDS)
    assert len({c.claim_id for c in run(model, delta).claims}) == len(kinds)


def test_claim_values_match_sim_result(model, delta):
    r = run(model, delta)
    by_kind = {c.kind: c.value for c in r.claims}
    assert by_kind["baseline_breach_rate"] == r.baseline_breach_rate
    assert by_kind["breach_rate_with_delta"] == r.delta_breach_rate
    assert by_kind["median_min_balance"] == r.median_min_balance
    assert by_kind["paths_simulated"] == r.n_paths
    assert by_kind["culprit_label"] == r.culprit.label
    assert by_kind["culprit_amount"] == r.culprit.amount
    assert by_kind["culprit_marginal_contribution"] == r.culprit.marginal_contribution


def test_claims_skip_when_model_has_no_evidence(model_dict, delta):
    for key in ("income", "discretionary"):
        model_dict[key]["evidence"] = []
    for r in model_dict["recurring"]:
        r["evidence"] = []
    model_dict["evidence_index"] = []
    claims = run(CashflowModel.from_dict(model_dict), delta).claims
    assert claims == []


def test_run_id_is_deterministic_and_input_sensitive(model, delta):
    assert run(model, delta, n_paths=20).run_id == run(model, delta, n_paths=20).run_id
    assert run(model, delta, n_paths=20).run_id != run(model, delta, n_paths=20, seed=2).run_id
    assert run(model, delta, n_paths=20, run_id="fixed").run_id == "fixed"


# Allowlist, not denylist: anything new (network client, unseeded random) has to be argued in here.
ALLOWED_MODULES = {"__future__", "core", "dataclasses", "hashlib", "json", "numpy", "pathlib", "sys", "typing", "uuid"}
FORBIDDEN_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("time", "time"), ("uuid", "uuid4")}


def _core_sources():
    return [p for p in CORE.rglob("*.py") if "tests" not in p.parts]


def test_only_allowlisted_imports():
    for path in _core_sources():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in ALLOWED_MODULES, f"{path.name} imports {name}"


def test_no_wall_clock_or_random_uuid():
    for path in _core_sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in FORBIDDEN_CALLS, f"{path.name} calls {node.value.id}.{node.attr}"


def test_demo_runs(capsys):
    demo_main([])
    out = json.loads(capsys.readouterr().out)
    assert out["n_paths"] == 500
    assert out["claims"]
