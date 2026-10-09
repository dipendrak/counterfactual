from copy import deepcopy
from datetime import date, timedelta

import httpx
import pytest

from core.model import DecisionDelta
from core.run import run
from data.cache import CacheRepository, cache_path, write_snapshot
from data.demo import demo_snapshots
from data.fit import FitError, fit
from data.ingest import IngestError, NessieClient, ingest, probe


@pytest.fixture
def snapshot():
    return demo_snapshots()[0]


def test_five_profiles_roundtrip_and_drive_real_core(tmp_path):
    for snapshot in demo_snapshots():
        write_snapshot(tmp_path, snapshot)
    repo = CacheRepository(tmp_path)
    assert len(repo.customers()) == 5
    for snapshot in repo.snapshots.values():
        model = fit(snapshot)
        assert model.income.evidence and model.discretionary.evidence
        assert all(r.evidence for r in model.recurring)
        assert all(repo.transaction(tid)["_id"] == tid for tid in model.evidence_index)
        assert all(r.cadence_days == int(r.cadence_days) for r in model.recurring)
        assert model.income.cadence_days == int(model.income.cadence_days)
        result = run(model, DecisionDelta("one_time", 1400, 0))
        assert len(result.paths) == 500
        assert all(claim.evidence for claim in result.claims)


def test_recurring_essential_and_discretionary(snapshot):
    model = fit(snapshot)
    by_label = {r.label: r for r in model.recurring}
    assert set(by_label) == {"Rent", "Geico"}
    assert by_label["Rent"].essential and not by_label["Geico"].essential
    assert by_label["Rent"].cadence_days == 30
    assert 0 < by_label["Rent"].next_due_day <= 30
    assert model.discretionary.by_category == {"food": 1.0}


@pytest.mark.parametrize("mutation", ["spacing", "amount"])
def test_irregular_merchant_is_discretionary(snapshot, mutation):
    rows = snapshot["accounts"][0]["purchases"]
    rent = [r for r in rows if "rent_" in r["merchant_id"]]
    if mutation == "amount":
        rent[0]["amount"] *= 2
    else:
        rent[0]["purchase_date"] = (date.fromisoformat(rent[0]["purchase_date"]) + timedelta(days=9)).isoformat()
    model = fit(snapshot)
    assert "Rent" not in [r.label for r in model.recurring]
    assert set(r["_id"] for r in rent) <= set(model.discretionary.evidence)


def test_irregular_income_retains_evidence_and_variability():
    snapshot = demo_snapshots()[1]
    model = fit(snapshot)
    assert model.income.std > 0
    assert len(model.income.evidence) == len(snapshot["accounts"][0]["deposits"])
    assert model.income.cadence_days > 0


def test_weekly_stats_include_empty_weeks(snapshot):
    snapshot["accounts"][0]["purchases"] = [
        {"_id": "once", "purchase_date": snapshot["as_of"], "amount": 70, "status": "completed"}]
    # Keep income in this window by moving three payments.
    rows = snapshot["accounts"][0]["deposits"][:3]
    for i, row in enumerate(rows):
        row["transaction_date"] = (date.fromisoformat(snapshot["as_of"]) - timedelta(days=i * 7)).isoformat()
    snapshot["accounts"][0]["deposits"] = rows
    model = fit(snapshot, window_days=28)
    assert model.discretionary.weekly_mean == 17.5
    assert model.discretionary.weekly_std == round(70 * (3 ** .5) / 4, 2)


def test_pending_old_and_credit_records_do_not_enter_model(snapshot):
    bundle = snapshot["accounts"][0]
    bundle["purchases"].extend([
        {"_id": "pending", "purchase_date": snapshot["as_of"], "amount": 9000, "status": "pending"},
        {"_id": "old", "purchase_date": "2020-01-01", "amount": 9000, "status": "completed"}])
    credit = deepcopy(bundle)
    credit["account"].update(_id="credit", type="Credit Card", balance=500000)
    snapshot["accounts"].append(credit)
    model = fit(snapshot)
    assert model.opening_balance == 2310.40
    assert not {"pending", "old"} & set(model.evidence_index)


def test_paid_bills_without_merchant_keep_original_ids(snapshot):
    bundle = snapshot["accounts"][0]
    for i in range(3):
        bundle["bills"].append({"_id": f"bill_{i}", "payee": "Student loan", "status": "completed",
                               "amount": 100, "payment_date": (date.fromisoformat(snapshot["as_of"]) - timedelta(days=10 + i * 30)).isoformat()})
    bundle["bills"].append({"_id": "schedule", "payee": "Student loan", "status": "pending", "amount": 100})
    model = fit(snapshot)
    bill = next(r for r in model.recurring if r.label == "Student loan")
    assert bill.merchant_id is None and bill.essential
    assert bill.evidence == ["bill_0", "bill_1", "bill_2"]
    assert "schedule" not in model.evidence_index


def test_production_bill_payment_amount_is_fitted(snapshot):
    bundle = snapshot["accounts"][0]
    bundle["bills"] = [{"_id": f"production_bill_{i}", "payee": "Student loan", "status": "completed",
                         "payment_amount": 100, "payment_date": (date.fromisoformat(snapshot["as_of"]) - timedelta(days=10 + i * 30)).isoformat()}
                        for i in range(3)]
    obligation = next(r for r in fit(snapshot).recurring if r.label == "Student loan")
    assert obligation.amount == 100
    assert obligation.evidence == ["production_bill_0", "production_bill_1", "production_bill_2"]


def test_external_outbound_transfers_and_withdrawals_are_expenses(snapshot):
    bundle = snapshot["accounts"][0]
    aid = bundle["account"]["_id"]
    bundle["withdrawals"] = [{"_id": "atm", "amount": 50, "transaction_date": snapshot["as_of"], "status": "completed"}]
    bundle["transfers"] = [
        {"_id": "external", "payer_id": aid, "payee_id": "other", "amount": 50, "transaction_date": snapshot["as_of"], "status": "completed"},
        {"_id": "internal", "payer_id": aid, "payee_id": aid, "amount": 50, "transaction_date": snapshot["as_of"], "status": "completed"}]
    model = fit(snapshot)
    assert {"atm", "external"} <= set(model.discretionary.evidence)
    assert "internal" not in model.evidence_index


@pytest.mark.parametrize("problem", ["income", "date", "nan"])
def test_unsupported_history_fails_honestly(snapshot, problem):
    if problem == "income":
        snapshot["accounts"][0]["deposits"] = []
    elif problem == "date":
        snapshot["accounts"][0]["deposits"][0]["transaction_date"] = "bad"
    else:
        snapshot["accounts"][0]["account"]["balance"] = float("nan")
    with pytest.raises(FitError):
        fit(snapshot)


def test_zero_discretionary_is_zero_and_has_no_invented_ids(snapshot):
    snapshot["accounts"][0]["purchases"] = [r for r in snapshot["accounts"][0]["purchases"] if "grocer" not in r["merchant_id"]]
    disc = fit(snapshot).discretionary
    assert disc.weekly_mean == disc.weekly_std == 0
    assert disc.evidence == []


def test_overdrawn_opening_balance_is_valid(snapshot):
    snapshot["accounts"][0]["account"]["balance"] = -125
    model = fit(snapshot)
    assert model.opening_balance == -125
    assert run(model, DecisionDelta("one_time", 1400, 0)).delta_breach_rate == 1


def test_cache_rejects_paths_missing_ids_and_conflicts(snapshot, tmp_path):
    with pytest.raises(ValueError):
        cache_path(tmp_path, "../oops")
    snapshot["accounts"][0]["deposits"][0].pop("_id")
    with pytest.raises(ValueError):
        write_snapshot(tmp_path, snapshot)
    assert not list(tmp_path.glob("*.json"))


def test_cache_atomic_replacement_preserves_good_snapshot(snapshot, tmp_path):
    path = write_snapshot(tmp_path, snapshot)
    original = path.read_text()
    snapshot["accounts"][0]["account"]["balance"] = float("nan")
    with pytest.raises(ValueError):
        write_snapshot(tmp_path, snapshot)
    assert path.read_text() == original


def test_ingest_fetches_all_resources_and_missing_merchant():
    requests = []
    def handler(request):
        requests.append(request.url.path)
        assert request.url.params["key"] == "secret"
        path = request.url.path
        if path.endswith("/accounts"):
            payload = [{"_id": "a", "type": "Checking", "balance": 10}]
        elif path.endswith("/purchases"):
            payload = [{"_id": "p", "merchant_id": "m"}]
        elif path == "/merchants/m":
            payload = {"_id": "m", "name": "Merchant"}
        else:
            payload = []
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        nessie = NessieClient("secret", "https://nessie.test", client)
        snapshot = nessie.snapshot({"_id": "customer"}, [])
    assert all(f"/accounts/a/{collection}" in requests for collection in ("purchases", "deposits", "bills", "withdrawals", "transfers", "loans"))
    assert snapshot["merchants"][0]["_id"] == "m"
    assert snapshot["accounts"][0]["purchases"][0] == {"_id": "p", "merchant_id": "m"}
    assert "secret" not in str(snapshot)


def test_ingest_pagination_and_cross_origin_rejection():
    def handler(request):
        if request.url.path == "/first":
            return httpx.Response(200, json={"data": [{"_id": "one"}], "paging": {"next": "/second"}})
        return httpx.Response(200, json=[{"_id": "two"}])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        nessie = NessieClient("secret", "https://nessie.test", client)
        assert [r["_id"] for r in nessie.list("/first")] == ["one", "two"]
        with pytest.raises(IngestError):
            nessie.get("https://elsewhere.test/steal")


def test_ingest_errors_redact_query_key():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401))) as client:
        nessie = NessieClient("top-secret", "https://nessie.test", client)
        with pytest.raises(IngestError) as exc:
            nessie.list("/customers")
    assert "401" in str(exc.value) and "top-secret" not in str(exc.value)


def test_404_transfer_route_and_deleted_merchant_are_explicit():
    def handler(request):
        path = request.url.path
        if path == "/customers/customer/accounts":
            return httpx.Response(200, json=[{"_id": "a", "type": "Checking", "balance": 10}])
        if path == "/accounts/a/purchases":
            return httpx.Response(200, json=[{"_id": "original_transaction", "merchant_id": "deleted"}])
        if path in ("/accounts/a/transfers", "/merchants/deleted"):
            return httpx.Response(404)
        return httpx.Response(200, json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = NessieClient("secret", "https://nessie.test", client).snapshot({"_id": "customer"}, [])
    assert snapshot["unavailable_collections"] == [{"account_id": "a", "collection": "transfers", "status": 404}]
    assert snapshot["unavailable_merchants"] == ["deleted"]
    assert snapshot["merchants"] == []
    assert CacheRepository.from_snapshots([snapshot]).transaction("original_transaction")["_id"] == "original_transaction"


def test_transfer_authorization_errors_are_never_treated_as_empty():
    def handler(request):
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json=[{"_id": "a"}])
        if request.url.path.endswith("/transfers"):
            return httpx.Response(403)
        return httpx.Response(200, json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(IngestError) as exc:
            NessieClient("secret", "https://nessie.test", client).snapshot({"_id": "customer"}, [])
    assert exc.value.status_code == 403


def test_auto_uses_accessible_enterprise_population_when_owned_is_empty(tmp_path, monkeypatch):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/enterprise/customers":
            return httpx.Response(200, json=[{"_id": "customer", "first_name": "Synthetic"}])
        return httpx.Response(200, json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr("data.ingest.NessieClient", lambda key, origin: NessieClient(key, origin, client))
        result = ingest("secret", tmp_path, base_url="https://nessie.test")
    assert paths[:2] == ["/customers", "/enterprise/customers"]
    assert result["population_endpoint"] == "/enterprise/customers"
    assert len(result["cached"]) == 1
    assert (tmp_path / "reports" / "customers.json").is_file()
    assert (tmp_path / "reports" / "ingest.json").is_file()


def test_owned_population_never_silently_expands_to_enterprise(tmp_path, monkeypatch):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr("data.ingest.NessieClient", lambda key, origin: NessieClient(key, origin, client))
        result = ingest("secret", tmp_path, base_url="https://nessie.test", population="owned")
    assert "/enterprise/customers" not in paths and result["cached"] == []


def test_pinned_origin_error_reports_actual_failure(tmp_path, monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401))) as client:
        monkeypatch.setattr("data.ingest.NessieClient", lambda key, origin: NessieClient(key, origin, client))
        with pytest.raises(IngestError) as exc:
            ingest("top-secret", tmp_path, base_url="https://nessie.test")
    assert "401" in str(exc.value) and "top-secret" not in str(exc.value)
