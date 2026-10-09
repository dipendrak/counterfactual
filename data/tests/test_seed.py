import copy
import json

import httpx
import pytest

from data.ingest import NessieClient, IngestError
from data.demo import demo_snapshots
from data.cache import CacheRepository
from data.seed import ORIGIN, SeedError, Seeder, validate_plan, build_plan
from data.money import USD, USD_CENTS, to_cents, RETIRED_ACCOUNT_PREFIX


def plan():
    snapshots = demo_snapshots()
    return {"as_of": snapshots[0]["as_of"], "profiles": [
        {"profile": s["customer"]["_id"], "customer": s["customer"],
         "account": s["accounts"][0]["account"], "merchants": s["merchants"],
         "deposits": s["accounts"][0]["deposits"], "purchases": s["accounts"][0]["purchases"]}
        for s in snapshots]}


def test_fresh_checkout_plan_contains_the_approved_population():
    p = build_plan()
    assert len(p["profiles"]) == 5
    assert sum(len(s["deposits"]) for s in p["profiles"]) == 114
    assert sum(len(s["purchases"]) for s in p["profiles"]) == 380
    assert len(validate_plan(p)) == 519
    assert p["profiles"][0]["account"]["balance"] == 2310.4


class Bank:
    def __init__(self, *, timeout_after_creation=False, bad_id=False, pending=False):
        self.rows = {}
        self.posts = 0
        self.timeout = timeout_after_creation
        self.bad_id = bad_id
        self.pending = pending

    def __call__(self, request):
        path = request.url.path
        if request.method == "GET":
            return httpx.Response(200, json=self.rows.get(path, []))
        self.posts += 1
        payload = json.loads(request.content)
        row = {**payload, "_id": "demo_steady" if self.bad_id else f"server_{self.posts}"}
        if self.pending:
            row["status"] = "pending"
        self.rows.setdefault(path, []).append(row)
        if self.timeout:
            self.timeout = False
            raise httpx.ReadTimeout("secret URL", request=request)
        return httpx.Response(201, json={"objectCreated": row})


def seeder(tmp_path, bank, p=None):
    client = httpx.Client(transport=httpx.MockTransport(bank))
    return Seeder(p or plan(), tmp_path / "journal.json", NessieClient("TEST_SECRET", ORIGIN, client))


def test_timeout_reconciles_without_second_post(tmp_path):
    bank = Bank(timeout_after_creation=True)
    seed = seeder(tmp_path, bank)
    with pytest.raises(SeedError, match="Uncertain") as exc:
        seed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    assert "secret" not in str(exc.value)
    assert json.loads(seed.path.read_text())["operations"]["demo_steady"]["state"] == "pending"
    resumed = seeder(tmp_path, bank)
    assert resumed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"}) == "server_1"
    assert bank.posts == 1
    assert resumed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"}) == "server_1"
    assert bank.posts == 1


def test_delayed_creation_visibility_retries_reads_only(tmp_path):
    bank = Bank()
    hidden = [False]
    def handler(request):
        if request.method == "POST":
            response = bank(request)
            hidden[0] = True
            return response
        if hidden[0]:
            hidden[0] = False
            return httpx.Response(200, json=[])
        return bank(request)
    seed = seeder(tmp_path, handler)
    assert seed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"}) == "server_1"
    assert bank.posts == 1


def test_unresolved_timeout_never_retries(tmp_path):
    bank = Bank(timeout_after_creation=True)
    seed = seeder(tmp_path, bank)
    with pytest.raises(SeedError):
        seed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    bank.rows["/customers"] = []
    with pytest.raises(SeedError, match="no POST retry"):
        seeder(tmp_path, bank).create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    assert bank.posts == 1


def test_duplicate_matches_halt(tmp_path):
    bank = Bank(timeout_after_creation=True)
    seed = seeder(tmp_path, bank)
    with pytest.raises(SeedError):
        seed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    bank.rows["/customers"].append({"first_name": "Alex", "_id": "server_2"})
    with pytest.raises(SeedError, match="2 matches"):
        seeder(tmp_path, bank).create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    assert bank.posts == 1


def test_fixture_id_and_scheduled_status_are_rejected(tmp_path):
    seed = seeder(tmp_path, Bank(bad_id=True))
    with pytest.raises(SeedError, match="server ID"):
        seed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    other = tmp_path / "other"
    other.mkdir()
    seed = seeder(other, Bank(pending=True))
    with pytest.raises(SeedError, match="readback"):
        seed.create("demo_steady_income_0", "/deposits", "/deposits", {"status": "completed"})


def test_plan_and_origin_pin_before_mutation(tmp_path):
    bank = Bank()
    seed = seeder(tmp_path, bank)
    seed.save()
    changed = plan()
    changed["as_of"] = "2026-10-08"
    with pytest.raises(SeedError):
        seeder(tmp_path, bank, changed)
    with pytest.raises(SeedError, match="HTTPS"):
        Seeder(plan(), tmp_path / "other.json", NessieClient("key", "http://api.nessieisreal.com"))
    assert bank.posts == 0


def test_truncated_purchase_cannot_be_accepted_on_resume(tmp_path):
    bank = Bank()
    seed = seeder(tmp_path, bank)
    # Simulate production truncating an acknowledged purchase, then interrupt.
    ref = "demo_steady_spend_0"
    payload = {"amount": 94.5, "status": "completed"}
    seed.journal["operations"][ref] = {"state": "pending", "path": "/purchases",
        "listing": "/purchases", "payload": payload, "before_ids": [], "acknowledged_id": "server_1"}
    seed.save()
    bank.rows["/purchases"] = [{"_id": "server_1", "amount": 94, "status": "completed"}]
    with pytest.raises(SeedError):
        seeder(tmp_path, bank).create(ref, "/purchases", "/purchases", payload)
    assert ref not in seeder(tmp_path, bank).journal["mapping"]
    assert bank.posts == 0


def test_known_precision_limitation_blocks_all_network_activity(tmp_path):
    bank = Bank()
    seed = seeder(tmp_path, bank)
    seed.journal["precision_support"] = "integer_only_verified"
    seed.journal["storage_unit"] = USD
    seed.save()
    with pytest.raises(SeedError, match="exact cents"):
        seeder(tmp_path, bank).execute()
    assert not bank.rows and bank.posts == 0


def test_historical_balance_truncation_is_not_accepted(tmp_path):
    bank = Bank()
    seed = seeder(tmp_path, bank)
    seed.journal["operations"]["account_demo_steady"] = {
        "state": "done", "payload": {"balance": 2310.4}, "initial_balance_readback": 2310}
    seed.save()
    with pytest.raises(SeedError, match="exact amount"):
        seeder(tmp_path, bank).execute()
    assert bank.posts == 0


@pytest.mark.parametrize("damage", ["amount", "reference", "status", "duplicate"])
def test_bad_plan_is_rejected(damage):
    p = copy.deepcopy(plan())
    t = p["profiles"][0]["purchases"][0]
    if damage == "amount":
        t["amount"] = float("nan")
    elif damage == "reference":
        t["merchant_id"] = "foreign"
    elif damage == "status":
        t["status"] = "pending"
    else:
        t["_id"] = p["profiles"][0]["customer"]["_id"]
    with pytest.raises(SeedError):
        validate_plan(p)


@pytest.mark.parametrize("storage_unit,truncate", [(USD, False), (USD, True), (USD_CENTS, True)])
def test_execute_ingest_and_resume_preserves_exact_amounts(tmp_path, storage_unit, truncate):
    p = plan()
    p["profiles"] = p["profiles"][:1]
    p["profiles"][0]["deposits"] = p["profiles"][0]["deposits"][:3]
    p["profiles"][0]["purchases"] = p["profiles"][0]["purchases"][-3:]
    rows = {}
    posts = []

    def bank(request):
        path = request.url.path
        if request.method == "GET":
            if path.startswith("/accounts/") and len(path.split("/")) == 3:
                return httpx.Response(200, json=rows[path])
            if path.startswith("/customers/") and len(path.split("/")) == 3:
                return httpx.Response(200, json=rows[path])
            if path.endswith("/transfers"):
                return httpx.Response(404)
            return httpx.Response(200, json=rows.get(path, []))
        assert request.method == "POST"
        # Verify durable pending state exists before every request is delivered.
        journal = json.loads((tmp_path / "journal.json").read_text())
        assert any(op["state"] == "pending" and op["path"] == path for op in journal["operations"].values())
        body = json.loads(request.content)
        assert "_id" not in body
        posts.append(path)
        row = {**body, "_id": f"remote_{len(posts)}"}
        if truncate:
            for key in ("amount", "balance"):
                if key in row:
                    row[key] = int(row[key])
        if path.endswith("/accounts"):
            row["customer_id"] = path.split("/")[2]
            rows[f"/accounts/{row['_id']}"] = row
        elif path == "/customers":
            rows[f"/customers/{row['_id']}"] = row
        if path.endswith("/purchases"):
            row["payer_id"] = path.split("/")[2]
        if path.endswith("/deposits"):
            row["payee_id"] = path.split("/")[2]
        rows.setdefault(path, []).append(row)
        return httpx.Response(201, json={"objectCreated": row})

    nessie = NessieClient("secret", ORIGIN, httpx.Client(transport=httpx.MockTransport(bank)))
    seed = Seeder(p, tmp_path / "journal.json", nessie)
    seed.storage_unit = storage_unit
    seed.journal.update(storage_unit=storage_unit, monetary_policy="integer_cents_storage" if storage_unit == USD_CENTS else "exact_cents")
    seed.save()
    if truncate and storage_unit == USD:
        with pytest.raises(SeedError, match="readback"):
            seed.execute()
        with pytest.raises(SeedError, match="no POST retry"):
            Seeder(p, seed.path, nessie).execute()
        assert len(posts) == 2
        assert seed.journal["status"] != "complete"
        return
    seed.execute()
    count = len(posts)
    assert seed.journal["status"] == "complete"
    Seeder(p, seed.path, nessie).execute()
    assert len(posts) == count == 11
    reports = seed.ingest(tmp_path / "cache")
    snapshot = CacheRepository(tmp_path / "cache").snapshot(reports[0]["customer_id"])
    assert snapshot["accounts"][0]["account"]["balance"] == (231040 if storage_unit == USD_CENTS else 2310.4)
    assert snapshot["seed_monetary_policy"] == seed.journal["monetary_policy"]
    stored = {t['_id']: t['amount'] for t in snapshot['accounts'][0]['purchases']}
    assert all(stored[seed.journal['mapping'][t['_id']]] == seed.money(t['amount']) for t in p['profiles'][0]['purchases'])
    assert len(CacheRepository(tmp_path / "cache").transactions) == 6
    assert not set(seed.refs) & set(CacheRepository(tmp_path / "cache").transactions)


def test_post_http_errors_do_not_leak_or_retry(tmp_path):
    posts = []
    def bank(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        posts.append(request)
        return httpx.Response(500, json={"message": "TEST_SECRET"})
    seed = seeder(tmp_path, bank)
    with pytest.raises(SeedError) as exc:
        seed.create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    assert "TEST_SECRET" not in str(exc.value)
    with pytest.raises(SeedError, match="no POST retry"):
        seeder(tmp_path, bank).create("demo_steady", "/customers", "/customers", {"first_name": "Alex"})
    assert len(posts) == 1


def test_interrupted_cent_migration_reuses_identities_and_retires_only_own_account(tmp_path):
    puts = []
    row = {"_id": "old_account", "customer_id": "customer", "nickname": "Synthetic demo_steady", "balance": 2310}
    fail_read = [False]
    def bank(request):
        assert request.url.path == "/accounts/old_account"
        if request.method == "PUT":
            puts.append(request)
            row.update(json.loads(request.content))
            fail_read[0] = True
            return httpx.Response(202)
        if fail_read[0]:
            fail_read[0] = False
            return httpx.Response(503)
        return httpx.Response(200, json=row)
    seed = seeder(tmp_path, bank)
    seed.storage_unit = USD
    seed.journal.update(storage_unit=USD, monetary_policy="exact_cents", status="blocked_exact_amounts")
    seed.journal["mapping"] = {"demo_steady": "customer", "account_demo_steady": "old_account", "rent_demo_steady": "merchant"}
    seed.journal["operations"]["account_demo_steady"] = {
        "state": "precision_mismatch", "server_id": "old_account", "payload": {"balance": 2310.4}}
    seed.journal["operations"]["demo_steady_spend_0"] = {
        "state": "precision_mismatch", "acknowledged_id": "old_purchase", "payload": {"amount": 94.5}}
    seed.save()
    with pytest.raises(IngestError):
        seed.migrate_to_cents()
    resumed = seeder(tmp_path, bank)
    resumed.migrate_to_cents()
    assert len(puts) == 1
    assert row["nickname"].startswith(RETIRED_ACCOUNT_PREFIX)
    assert resumed.storage_unit == USD_CENTS
    assert resumed.journal["mapping"] == {"demo_steady": "customer", "rent_demo_steady": "merchant"}
    assert resumed.journal["cent_migration"]["legacy_operations"]["demo_steady_spend_0"]["acknowledged_id"] == "old_purchase"
    assert seed.path.with_name("pre-cents-seed-journal.json").exists()
