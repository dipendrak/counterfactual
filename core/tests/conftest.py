from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from core.model import CashflowModel, DecisionDelta

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def model_dict() -> dict:
    return json.loads((FIXTURES / "model_fixture.json").read_text())


@pytest.fixture
def delta_dict() -> dict:
    return json.loads((FIXTURES / "delta_fixture.json").read_text())


@pytest.fixture
def model(model_dict) -> CashflowModel:
    return CashflowModel.from_dict(model_dict)


@pytest.fixture
def delta(delta_dict) -> DecisionDelta:
    return DecisionDelta.from_dict(delta_dict)


@pytest.fixture
def dominant_model_dict(model_dict) -> dict:
    """Tight budget where a weekly bill dominates, despite a bigger per-payment monthly bill."""
    d = copy.deepcopy(model_dict)
    d["opening_balance"] = 500.0
    d["income"]["mean"] = 1200.0
    d["recurring"] = [
        {"obligation_id": "rec_big_monthly", "label": "Rent", "merchant_id": None, "amount": 500.0,
         "cadence_days": 30, "next_due_day": 5, "evidence": ["txn_s1"]},
        {"obligation_id": "rec_villain", "label": "Payday lender", "merchant_id": None, "amount": 350.0,
         "cadence_days": 7, "next_due_day": 2, "evidence": ["txn_v1", "txn_v2"]},
        {"obligation_id": "rec_small_b", "label": "iCloud", "merchant_id": None, "amount": 2.99,
         "cadence_days": 30, "next_due_day": 9, "evidence": ["txn_i1"]},
    ]
    return d
