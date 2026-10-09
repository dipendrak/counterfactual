"""Create the approved engineered Nessie population with a durable operation journal.

POSTs are never retried after an uncertain outcome. A pending operation is
reconciled by its exact payload and pre-request ID set, or execution halts.
Only this journal's newly created accounts can be updated. Runtime remains offline.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv

from data.cache import record_id, write_snapshot
from data.ingest import IngestError, NessieClient
from data.demo import demo_snapshots
from data.money import (USD, USD_CENTS, CENTS_ACCOUNT_PREFIX, RETIRED_ACCOUNT_PREFIX,
                        to_cents, to_dollars)

ORIGIN = "https://prod-api.nessieisreal.com"


class SeedError(RuntimeError):
    pass


def build_plan() -> dict:
    """Reproduce the five designed profiles locally on a fresh checkout."""
    snapshots = demo_snapshots()
    return {"status": "proposal_only_not_sent_to_nessie", "as_of": snapshots[0]["as_of"],
            "source": "engineered_synthetic_population",
            "note": "Nessie must generate all resource IDs; fixture IDs below are local references only.",
            "profiles": [{"profile": s["customer"]["_id"], "customer": s["customer"],
                          "account": s["accounts"][0]["account"], "merchants": s["merchants"],
                          "deposits": s["accounts"][0]["deposits"], "purchases": s["accounts"][0]["purchases"]}
                         for s in snapshots]}


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
            temporary = f.name
            json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def validate_plan(plan: dict) -> set[str]:
    """Reject invalid references/amounts before any network mutation."""
    date.fromisoformat(plan["as_of"])
    refs: set[str] = set()
    profiles = plan["profiles"]
    if not profiles:
        raise SeedError("Seed plan is empty")
    for p in profiles:
        cid, aid = record_id(p["customer"]), record_id(p["account"])
        if p["profile"] != cid or p["account"]["customer_id"] != cid:
            raise SeedError("Invalid customer/account reference")
        if p["account"]["type"] != "Checking":
            raise SeedError("Only approved checking accounts can be seeded")
        merchants = {record_id(m) for m in p["merchants"]}
        rows = [p["customer"], p["account"], *p["merchants"], *p["deposits"], *p["purchases"]]
        for row in rows:
            ref = record_id(row)
            if ref in refs:
                raise SeedError("Duplicate plan reference")
            refs.add(ref)
        for row in [p["account"], *p["deposits"], *p["purchases"]]:
            amount = row.get("amount", row.get("balance"))
            if isinstance(amount, bool) or not isinstance(amount, (float, int)) or not math.isfinite(amount) or amount < 0:
                raise SeedError("Invalid seed amount")
            to_cents(amount)  # Exact cents only; never silently round the approved plan.
        for row in p["deposits"] + p["purchases"]:
            if row["status"] != "completed":
                raise SeedError("History must be posted")
            day = date.fromisoformat(row.get("purchase_date", row.get("transaction_date")))
            if day > date.fromisoformat(plan["as_of"]):
                raise SeedError("Future transaction in history")
            if row.get("payer_id", row.get("payee_id")) != aid:
                raise SeedError("Invalid transaction account reference")
            if "merchant_id" in row and row["merchant_id"] not in merchants:
                raise SeedError("Invalid merchant reference")
    return refs


def matches(record: dict, payload: dict) -> bool:
    # Account owner IDs and server-added fields are verified separately.
    return all(record.get(k) == v for k, v in payload.items())


class Seeder:
    def __init__(self, plan: dict, journal_path: Path, nessie: NessieClient):
        self.refs = validate_plan(plan)
        if nessie.base_url != ORIGIN:
            raise SeedError("Seed writes require the pinned production HTTPS origin")
        self.plan, self.path, self.nessie = plan, journal_path, nessie
        self.digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.tag = self.digest[:12]
        self.journal = json.loads(self.path.read_text()) if self.path.exists() else {
            "schema_version": 2, "plan_hash": self.digest, "origin": ORIGIN,
            "storage_unit": USD_CENTS, "monetary_policy": "integer_cents_storage",
            "status": "in_progress", "operations": {}, "mapping": {}, "profiles": {}}
        if self.journal["plan_hash"] != self.digest or self.journal["origin"] != ORIGIN:
            raise SeedError("Journal does not match this plan/origin")
        if self.journal.get("monetary_policy", "exact_cents") not in ("exact_cents", "integer_cents_storage"):
            raise SeedError("Journal monetary policy does not preserve approved exact amounts")
        self.storage_unit = self.journal.get("storage_unit", USD)
        if self.storage_unit not in (USD, USD_CENTS):
            raise SeedError("Unknown seed storage unit")

    def money(self, value):
        return to_cents(value) if self.storage_unit == USD_CENTS else value

    def migrate_to_cents(self):
        """Retain dollar records, reuse customer/merchants, replace monetary records.

        AccountUpdate cannot set balance on production. Replacing the partial
        account is necessary to preserve its exact approved cents. No DELETEs.
        Every migration step is durable; only fixed-value PUTs can be resumed.
        """
        if self.storage_unit == USD_CENTS:
            return
        if "cent_migration" not in self.journal:
            atomic_json(self.path.with_name("pre-cents-seed-journal.json"), self.journal)
            monetary_refs = {record_id(row) for p in self.plan["profiles"]
                             for row in [p["account"], *p["deposits"], *p["purchases"]]}
            legacy = {ref: op for ref, op in self.journal["operations"].items() if ref in monetary_refs}
            for ref, op in legacy.items():
                if not (op.get("server_id") or op.get("acknowledged_id")):
                    raise SeedError(f"Resolve uncertain legacy operation {ref} before migration; no POST retry")
            accounts = []
            for p in self.plan["profiles"]:
                op = legacy.get(record_id(p["account"]))
                if op:
                    accounts.append({"profile": p["profile"], "account_id": op.get("server_id") or op["acknowledged_id"],
                                     "customer_id": self.journal["mapping"][p["profile"]], "state": "pending"})
            self.journal["cent_migration"] = {"state": "pending", "legacy_operations": legacy, "accounts": accounts}
            self.save()
        migration = self.journal["cent_migration"]
        for entry in migration["accounts"]:
            aid = entry["account_id"]
            record = self.nessie.get(f"/accounts/{aid}")
            if record_id(record) != aid or record.get("customer_id") != entry["customer_id"]:
                raise SeedError("Legacy account migration identity mismatch")
            nickname = f"{RETIRED_ACCOUNT_PREFIX}{entry['profile']} {self.tag}"
            if record.get("nickname") != nickname:
                self.request("PUT", f"/accounts/{aid}", {"nickname": nickname})
            if self.nessie.get(f"/accounts/{aid}").get("nickname") != nickname:
                raise SeedError("Legacy account retirement could not be verified")
            entry["state"] = "retired"
            self.save()
        for ref in migration["legacy_operations"]:
            self.journal["operations"].pop(ref, None)
            self.journal["mapping"].pop(ref, None)
        self.journal["profiles"] = {}
        migration["state"] = "complete"
        self.journal.update(schema_version=2, storage_unit=USD_CENTS,
                            monetary_policy="integer_cents_storage", status="in_progress")
        self.storage_unit = USD_CENTS
        self.save()

    def matches(self, record, payload):
        return matches(record, payload)

    def save(self):
        atomic_json(self.path, self.journal)

    def request(self, method: str, path: str, payload: dict):
        try:
            response = self.nessie.client.request(method, ORIGIN + path,
                params={"key": self.nessie.api_key}, json=payload)
        except httpx.HTTPError:
            raise SeedError(f"Uncertain {method} outcome at {path}; reconcile before continuing") from None
        if not 200 <= response.status_code < 300:
            # Do not include body/URL: either can echo the secret.
            raise SeedError(f"Nessie {method} returned HTTP {response.status_code} at {path}")
        return response

    def accept(self, ref: str, row: dict, payload: dict):
        sid = record_id(row)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or sid in self.refs or sid in self.journal["mapping"].values():
            raise SeedError("Invalid, local, or duplicate server ID")
        if not self.matches(row, payload):
            raise SeedError(f"Readback differs from intended payload for {ref}")
        self.journal["mapping"][ref] = sid
        self.journal["operations"][ref].update(state="done", server_id=sid)
        if "balance" in payload:
            self.journal["operations"][ref]["initial_balance_readback"] = row.get("balance")
        if "amount" in payload:
            self.journal["operations"][ref]["amount_readback"] = row.get("amount")
        self.save()
        return sid

    def create(self, ref: str, path: str, listing: str, payload: dict):
        ops = self.journal["operations"]
        op = ops.get(ref)
        if op and (op["path"] != path or op["payload"] != payload):
            raise SeedError(f"Operation payload changed for {ref}")
        if op and op["state"] == "done":
            return self.journal["mapping"][ref]
        rows = self.nessie.list(listing)
        if op:
            candidates = [r for r in rows if record_id(r) not in op["before_ids"] and self.matches(r, payload)]
            if len(candidates) == 1:
                if op.get("acknowledged_id") not in (None, record_id(candidates[0])):
                    raise SeedError("Response ID and remote list disagree")
                return self.accept(ref, candidates[0], payload)
            raise SeedError(f"Unresolved operation {ref}: {len(candidates)} matches; no POST retry")
        if any(self.matches(r, payload) for r in rows):
            raise SeedError(f"Remote matching record without journal for {ref}; no creation")
        ops[ref] = {"state": "pending", "path": path, "listing": listing,
                    "payload": payload, "before_ids": [record_id(r) for r in rows]}
        self.save()  # durable before the request
        response = self.request("POST", path, payload)
        try:
            body = response.json()
        except ValueError:
            body = None
        created = body.get("objectCreated", body) if isinstance(body, dict) else None
        if isinstance(created, dict) and created.get("_id"):
            # Save the acknowledged ID even if a subsequent read fails.
            ops[ref]["acknowledged_id"] = record_id(created)
            self.save()
        # Production lists can briefly lag an acknowledged creation. Retry only
        # reads, never the POST; ambiguity still halts with the durable journal.
        candidates = []
        for attempt in range(3):
            rows = self.nessie.list(listing)
            candidates = [r for r in rows if record_id(r) not in ops[ref]["before_ids"] and self.matches(r, payload)]
            if candidates:
                break
            if attempt < 2:
                time.sleep(.2)
        if len(candidates) != 1:
            raise SeedError(f"Unresolved readback for {ref}; no POST retry")
        if ops[ref].get("acknowledged_id") not in (None, record_id(candidates[0])):
            raise SeedError("Response ID and remote list disagree")
        return self.accept(ref, candidates[0], payload)

    def execute(self, *, max_creates: int | None = None):
        if self.journal.get("cent_migration", {}).get("state") == "pending":
            self.migrate_to_cents()
        if self.storage_unit == USD and self.journal.get("precision_support") == "integer_only_verified":
            raise SeedError("Production Nessie cannot preserve this plan's exact cents; seed is blocked before further writes")
        # Validate historical acknowledgements before resuming. A server ID is
        # evidence of creation, never proof of the requested amount being saved.
        for op in self.journal["operations"].values():
            for field, observed_field in (("balance", "initial_balance_readback"), ("amount", "amount_readback")):
                if observed_field in op and op[observed_field] != op["payload"][field]:
                    raise SeedError("Existing seed record differs from approved exact amount; no further writes")
        count = 0

        def create(*args):
            nonlocal count
            if max_creates is not None and count >= max_creates:
                raise StopIteration
            sid = self.create(*args)
            count += 1
            return sid

        try:
            for p in self.plan["profiles"]:
                ref = p["profile"]
                address = {"street_number": "100", "street_name": f"Synthetic Seed {self.tag} {ref}",
                           "city": "McLean", "state": "VA", "zip": "22102"}
                cid = create(ref, "/customers", "/customers", {
                    "first_name": p["customer"]["first_name"], "last_name": f"Synthetic Seed {self.tag}", "address": address})
                prefix = CENTS_ACCOUNT_PREFIX if self.storage_unit == USD_CENTS else "Synthetic "
                account = {"type": "Checking", "nickname": f"{prefix}{ref} {self.tag}",
                           "rewards": 0, "balance": self.money(p["account"]["balance"])}
                aid = create(record_id(p["account"]), f"/customers/{cid}/accounts", f"/customers/{cid}/accounts", account)
                if self.nessie.get(f"/accounts/{aid}").get("customer_id") != cid:
                    raise SeedError("Created account has wrong owner")
                target_balance = account["balance"]
                self.journal["profiles"].setdefault(ref, {}).update(
                    customer_id=cid, account_id=aid, closing_balance=target_balance,
                    proposed_closing_balance=p["account"]["balance"], storage_unit=self.storage_unit)
                self.save()
                for m in p["merchants"]:
                    create(record_id(m), "/merchants", "/merchants", {
                        "name": m["name"], "category": m["category"][0],
                        "address": {**address, "street_number": str(p["merchants"].index(m) + 200)},
                        "geocode": {"lat": 38.9339, "lng": -77.1773}})
                for collection in ("deposits", "purchases"):
                    for t in sorted(p[collection], key=lambda t: t.get("purchase_date", t.get("transaction_date"))):
                        payload = {"medium": "balance", "amount": self.money(t["amount"]), "status": "completed"}
                        if collection == "deposits":
                            payload.update(transaction_date=t["transaction_date"], description=f"Payroll {self.tag} {ref}")
                        else:
                            payload.update(purchase_date=t["purchase_date"], merchant_id=self.journal["mapping"][t["merchant_id"]],
                                           description=f"Synthetic {self.tag} {record_id(t)}")
                        create(record_id(t), f"/accounts/{aid}/{collection}", f"/accounts/{aid}/{collection}", payload)
                observed = self.nessie.get(f"/accounts/{aid}")["balance"]
                if observed != target_balance:
                    # A set-value PUT is safe to resume, scoped to our own newly created account.
                    self.journal["profiles"][ref]["balance_before_correction"] = observed
                    self.save()
                    self.request("PUT", f"/accounts/{aid}", {"nickname": account["nickname"], "balance": target_balance})
                final = self.nessie.get(f"/accounts/{aid}")["balance"]
                if final != target_balance:
                    raise SeedError(f"Closing balance correction unsupported for {ref}; observed {final}")
                self.journal["profiles"][ref]["verified_balance"] = final
                self.save()
        except StopIteration:
            return self.journal
        self.journal["status"] = "complete"
        self.save()
        return self.journal

    def ingest(self, directory: Path):
        if self.journal["status"] != "complete":
            raise SeedError("Complete creation before ingestion")
        reports = []
        merchants = self.nessie.list("/merchants")
        for p in self.plan["profiles"]:
            ref = p["profile"]
            cid = self.journal["profiles"][ref]["customer_id"]
            snapshot = self.nessie.snapshot(self.nessie.get(f"/customers/{cid}"), merchants)
            aid = self.journal["profiles"][ref]["account_id"]
            bundles = [b for b in snapshot["accounts"] if b.get("include_in_fit", True)]
            if len(bundles) != 1 or record_id(bundles[0]["account"]) != aid:
                raise SeedError("Ingest account set differs from seed")
            for collection in ("deposits", "purchases"):
                expected = {self.journal["mapping"][record_id(t)] for t in p[collection]}
                rows = bundles[0][collection]
                if {record_id(t) for t in rows} != expected or any(t.get("status") != "completed" for t in rows):
                    raise SeedError("Ingest transaction IDs/statuses differ from seed")
                ops_by_id = {op.get("server_id"): op for op in self.journal["operations"].values()}
                for row in rows:
                    op = ops_by_id[record_id(row)]
                    if not self.matches(row, op["payload"]):
                        raise SeedError("Ingest transaction payload differs from seed")
            if bundles[0]["account"]["balance"] != self.journal["profiles"][ref]["closing_balance"]:
                raise SeedError("Ingest balance differs from approved closing balance")
            snapshot.update(population_origin="engineered_synthetic_population", seed_plan_hash=self.digest,
                            seed_profile_reference=ref, as_of=self.plan["as_of"],
                            seed_monetary_policy=self.journal.get("monetary_policy", "exact_cents"),
                            monetary_convention="User opted for integer USD cents storage; models/deltas use USD dollars")
            if bundles[0].get("monetary_unit", USD) != self.storage_unit:
                raise SeedError("Ingest storage-unit marker differs from seed")
            reports.append({"profile": ref, "customer_id": cid, "path": str(write_snapshot(directory, snapshot)),
                            "deposits": len(p["deposits"]), "purchases": len(p["purchases"]),
                            "storage_unit": self.storage_unit,
                            "closing_balance_usd": to_dollars(bundles[0]["account"]["balance"], self.storage_unit)})
        atomic_json(directory / "reports" / "ingest.json", {"origin": ORIGIN, "plan_hash": self.digest, "profiles": reports})
        return reports


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("data/cache/reports/proposed_population.json"))
    parser.add_argument("--journal", type=Path, default=Path("data/cache/seeded/reports/seed-journal.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/seeded"))
    parser.add_argument("--execute", action="store_true", help="Execute the already approved seed")
    parser.add_argument("--write-plan", action="store_true", help="Generate the local plan on a fresh checkout; refuses to overwrite an existing plan")
    parser.add_argument("--max-creates", type=int, help="Stop after this many operations for initial live verification")
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--migrate-to-cents", action="store_true", help="Migrate the earlier partial dollar seed to the user-approved integer-cent convention")
    args = parser.parse_args()
    nessie = None
    try:
        if args.write_plan:
            if args.execute:
                raise SeedError("Generate and review the plan separately from execution")
            if args.plan.exists():
                raise SeedError("Plan exists; refusing to overwrite its hash/history")
            atomic_json(args.plan, build_plan())
        plan = json.loads(args.plan.read_text())
        refs = validate_plan(plan)
        if not args.execute:
            print(json.dumps({"status": "validated_without_network", "resources": len(refs)}))
            return
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with args.journal.with_suffix(".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SeedError("Another seed executor holds the journal lock") from None
            nessie = NessieClient(os.getenv("NESSIE_API_KEY", ""), os.getenv("NESSIE_BASE_URL", ORIGIN))
            seed = Seeder(plan, args.journal, nessie)
            if args.migrate_to_cents:
                seed.migrate_to_cents()
            journal = seed.execute(max_creates=args.max_creates)
            if args.ingest:
                seed.ingest(args.cache_dir)
            print(json.dumps({"status": journal["status"], "plan_hash": seed.digest,
                              "created_resources": len(journal["mapping"]), "profiles": journal["profiles"]}, indent=2))
    except (SeedError, IngestError, ValueError, KeyError, OSError) as exc:
        print(f"Seed halted: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if nessie:
            nessie.close()


if __name__ == "__main__":
    main()
