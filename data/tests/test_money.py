from copy import deepcopy

import httpx
import pytest

from core.model import DecisionDelta
from core.run import run
from data.cache import CacheRepository
from data.demo import demo_snapshots
from data.fit import fit, FitError
from data.ingest import NessieClient
from data.money import (USD, USD_CENTS, CENTS_ACCOUNT_PREFIX, RETIRED_ACCOUNT_PREFIX,
                        to_cents, to_dollars)


def cents_snapshot(snapshot):
    result = deepcopy(snapshot)
    for bundle in result["accounts"]:
        bundle["monetary_unit"] = USD_CENTS
        bundle["account"]["nickname"] = CENTS_ACCOUNT_PREFIX + snapshot["customer"]["_id"]
        bundle["account"]["balance"] = to_cents(bundle["account"]["balance"])
        for collection in ("deposits", "purchases", "bills", "withdrawals", "transfers"):
            for row in bundle[collection]:
                field = "payment_amount" if collection == "bills" and "amount" not in row else "amount"
                row[field] = to_cents(row[field])
    return result


@pytest.mark.parametrize("amount,cents", [(94.5, 9450), (2310.4, 231040), (0.29, 29), (78.75, 7875), (0, 0)])
def test_exact_cent_conversion(amount, cents):
    assert to_cents(amount) == cents
    assert to_dollars(cents, USD_CENTS) == amount


@pytest.mark.parametrize("amount", [True, float("nan"), float("inf"), "invalid", "0.001", 1.001, "244.99999999999997"])
def test_no_silent_rounding(amount):
    with pytest.raises(ValueError):
        to_cents(amount)


def test_legacy_binary_float_residue_does_not_change_a_cent():
    assert to_cents(175 * 1.4) == 24500


def test_all_five_cent_models_and_simulations_equal_dollar_originals():
    for original in demo_snapshots():
        encoded = cents_snapshot(original)
        before = deepcopy(encoded)
        expected = fit(original)
        actual = fit(encoded)
        assert actual.to_dict() == expected.to_dict()
        assert run(actual, DecisionDelta("one_time", 1400, 0)).to_dict() == run(expected, DecisionDelta("one_time", 1400, 0)).to_dict()
        assert encoded == before  # The raw evidence remains cents with original IDs.
        repo = CacheRepository.from_snapshots([encoded])
        tid = encoded["accounts"][0]["purchases"][-1]["_id"]
        assert repo.transaction_unit(tid) == USD_CENTS


def test_mixed_units_and_superseded_account_do_not_double_count():
    original = demo_snapshots()[0]
    snapshot = cents_snapshot(original)
    retired = deepcopy(original["accounts"][0])
    retired["account"].update(_id="legacy", nickname=RETIRED_ACCOUNT_PREFIX + "legacy")
    snapshot["accounts"].append(retired)
    assert fit(snapshot).to_dict() == fit(original).to_dict()
    # Two active accounts, one dollars and one cents, are normalized individually.
    retired["account"]["nickname"] = "Ordinary dollars"
    for collection in ("deposits", "purchases"):
        retired[collection] = []
    assert fit(snapshot).opening_balance == 4620.8


def test_missing_or_invalid_units_fail_closed():
    snapshot = cents_snapshot(demo_snapshots()[0])
    snapshot["accounts"][0].pop("monetary_unit")
    with pytest.raises(ValueError, match="unit metadata"):
        fit(snapshot)
    snapshot["accounts"][0]["monetary_unit"] = "guess"
    with pytest.raises(ValueError, match="Unknown"):
        fit(snapshot)
    snapshot["accounts"][0]["monetary_unit"] = USD_CENTS
    snapshot["accounts"][0]["purchases"][0]["amount"] = 9450.5
    with pytest.raises(FitError):
        fit(snapshot)


def test_read_only_ingest_recognizes_durable_unit_and_retirement_markers():
    def handler(request):
        if request.url.path == "/customers/c/accounts":
            return httpx.Response(200, json=[
                {"_id": "cents", "nickname": CENTS_ACCOUNT_PREFIX + "c", "balance": 231040},
                {"_id": "legacy", "nickname": RETIRED_ACCOUNT_PREFIX + "c", "balance": 2310}])
        return httpx.Response(200, json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = NessieClient("secret", "https://nessie.test", client).snapshot({"_id": "c"}, [])
    assert snapshot["accounts"][0]["monetary_unit"] == USD_CENTS
    assert snapshot["accounts"][1]["monetary_unit"] == USD
    assert snapshot["accounts"][1]["include_in_fit"] is False
    assert snapshot["accounts"][0]["account"]["balance"] == 231040
