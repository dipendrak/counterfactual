"""Read-only Nessie ingestion. Run before the demo, never from an API request."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from dotenv import load_dotenv

from data.cache import COLLECTIONS, record_id, write_snapshot
from data.money import CENTS_ACCOUNT_PREFIX, RETIRED_ACCOUNT_PREFIX, USD, USD_CENTS

ORIGINS = ("https://prod-api.nessieisreal.com", "http://api.nessieisreal.com")


class IngestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class NessieClient:
    def __init__(self, api_key: str, base_url: str, client: httpx.Client | None = None):
        if not api_key:
            raise IngestError("Set NESSIE_API_KEY in .env or the environment")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.owns_client = client is None

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def get(self, path: str):
        url = urljoin(self.base_url + "/", path)
        # Pagination must not send the query-string secret to another origin.
        if urlsplit(url)[:2] != urlsplit(self.base_url)[:2]:
            raise IngestError("Nessie pagination changed origin")
        try:
            response = self.client.get(url, params={"key": self.api_key})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # httpx exceptions contain the URL including the secret. Never print them.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "network/JSON"
            raise IngestError(f"Nessie request failed ({status}) at {urlsplit(url).path}",
                              status_code=status if isinstance(status, int) else None) from None

    def list(self, path: str) -> list[dict]:
        records: list[dict] = []
        seen = set()
        while path:
            if path in seen:
                raise IngestError("Nessie pagination cycle")
            seen.add(path)
            payload = self.get(path)
            if isinstance(payload, list):
                page, next_path = payload, None
            elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                page = payload["data"]
                paging = payload.get("paging") or {}
                next_path = paging.get("next") or payload.get("next")
            else:
                raise IngestError(f"Expected record list at {urlsplit(path).path}")
            if any(not isinstance(record, dict) for record in page):
                raise IngestError("Nessie returned a non-object record")
            records.extend(page)
            path = next_path
        return records

    def snapshot(self, customer: dict, merchants: list[dict]) -> dict:
        cid = record_id(customer)
        accounts = []
        unavailable = []
        for account in self.list(f"/customers/{cid}/accounts"):
            aid = record_id(account)
            bundle = {"account": account}
            # Durable, explicit markers for the app's opted-in synthetic seed.
            # Ordinary Nessie accounts retain dollar semantics.
            nickname = str(account.get("nickname", ""))
            if nickname.startswith(CENTS_ACCOUNT_PREFIX):
                bundle["monetary_unit"] = USD_CENTS
            elif nickname.startswith(RETIRED_ACCOUNT_PREFIX):
                bundle.update(monetary_unit=USD, include_in_fit=False,
                              exclusion_reason="superseded partial dollar seed")
            for collection in COLLECTIONS:
                try:
                    bundle[collection] = self.list(f"/accounts/{aid}/{collection}")
                except IngestError as exc:
                    # These legacy list routes can be absent on the production
                    # origin. Record that fact rather than claiming observed zero.
                    if collection not in ("transfers", "loans") or exc.status_code != 404:
                        raise
                    bundle[collection] = []
                    unavailable.append({"account_id": aid, "collection": collection, "status": 404})
            accounts.append(bundle)
        merchant_index = {record_id(m): m for m in merchants}
        unavailable_merchants = []
        needed = {r["merchant_id"] for a in accounts for r in a["purchases"] if r.get("merchant_id")}
        for mid in sorted(needed - merchant_index.keys()):
            try:
                merchant = self.get(f"/merchants/{mid}")
            except IngestError as exc:
                if exc.status_code != 404:
                    raise
                unavailable_merchants.append(mid)
                continue
            if not isinstance(merchant, dict) or record_id(merchant) != mid:
                raise IngestError("Invalid merchant response")
            merchant_index[mid] = merchant
        now = datetime.now(timezone.utc)
        return {"schema_version": 1, "source": "nessie", "base_url": self.base_url,
                "ingested_at": now.isoformat(), "as_of": now.date().isoformat(),
                "customer": customer, "accounts": accounts,
                "merchants": [merchant_index[mid] for mid in sorted(needed) if mid in merchant_index],
                "unavailable_collections": unavailable,
                "unavailable_merchants": unavailable_merchants}


def probe(api_key: str, base_url: str) -> dict:
    """Read-only population diagnosis; returns counts and schemas, never credentials."""
    nessie = NessieClient(api_key, base_url)
    report = {"origin": base_url, "resources": {}}
    try:
        enterprise = []
        for path in ("/customers", "/enterprise/customers", "/accounts", "/merchants"):
            try:
                records = nessie.list(path)
                if path == "/enterprise/customers":
                    enterprise = records
                report["resources"][path] = {
                    "count": len(records), "sample_fields": sorted(records[0]) if records else []}
            except IngestError as exc:
                report["resources"][path] = {"error": str(exc)}
        samples = []
        for customer in enterprise[:3]:
            cid = record_id(customer)
            sample = {"customer_id": cid, "declared_accounts": len(customer.get("account_ids", []))}
            try:
                accounts = nessie.list(f"/customers/{cid}/accounts")
                sample["accessible_accounts"] = len(accounts)
                if accounts:
                    aid = record_id(accounts[0])
                    sample["account_fields"] = sorted(accounts[0])
                    sample["collections"] = {}
                    for collection in COLLECTIONS:
                        try:
                            rows = nessie.list(f"/accounts/{aid}/{collection}")
                            sample["collections"][collection] = {"count": len(rows), "sample_fields": sorted(rows[0]) if rows else []}
                        except IngestError as exc:
                            sample["collections"][collection] = {"error": str(exc)}
            except IngestError as exc:
                sample["error"] = str(exc)
            samples.append(sample)
        report["enterprise_samples"] = samples
        return report
    finally:
        nessie.close()


def ingest(api_key: str, directory: Path, *, base_url: str | None = None,
           count: int = 5, customer_ids: list[str] | None = None, population: str = "auto",
           offset: int = 0) -> dict:
    if count < 1:
        raise IngestError("count must be positive")
    if offset < 0:
        raise IngestError("offset must be nonnegative")
    if population not in ("auto", "owned", "enterprise"):
        raise IngestError("population must be auto, owned, or enterprise")
    nessie = None
    customers = None
    listing = "/enterprise/customers" if population == "enterprise" else "/customers"
    failures = []
    for origin in ((base_url,) if base_url else ORIGINS):
        candidate = NessieClient(api_key, origin)
        try:
            customers = candidate.list(listing)
            if not customers and population == "auto":
                listing = "/enterprise/customers"
                customers = candidate.list(listing)
            nessie = candidate
            break
        except IngestError as exc:
            failures.append(str(exc))
            candidate.close()
    if nessie is None:
        raise IngestError("Nessie population request failed: " + "; ".join(failures))
    try:
        by_id = {record_id(c): c for c in customers}
        catalog_dir = directory / "reports"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        (catalog_dir / "customers.json").write_text(json.dumps(customers, indent=2, allow_nan=False))
        if customer_ids:
            missing = set(customer_ids) - by_id.keys()
            if missing:
                raise IngestError(f"Customers are not available for this key: {sorted(missing)}")
            selected = [by_id[cid] for cid in dict.fromkeys(customer_ids)]
        else:
            selected = customers[offset:offset + count]
        merchants = nessie.list("/merchants")
        cached = []
        skipped = []
        for customer in selected:
            try:
                snapshot = nessie.snapshot(customer, merchants)
                cached.append(str(write_snapshot(directory, snapshot)))
            except (IngestError, ValueError) as exc:
                skipped.append({"customer_id": record_id(customer), "reason": str(exc)})
        report = {"origin": nessie.base_url, "population_endpoint": listing, "cached": cached, "skipped": skipped,
                "requested": len(customer_ids) if customer_ids else count,
                "available_customers": len(customers)}
        (catalog_dir / "ingest.json").write_text(json.dumps(report, indent=2, allow_nan=False))
        return report
    finally:
        nessie.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--customer-id", action="append")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.getenv("COUNTERFACTUAL_CACHE_DIR", "data/cache")))
    parser.add_argument("--base-url", default=os.getenv("NESSIE_BASE_URL") or None)
    parser.add_argument("--probe", action="store_true", help="Check available populations without caching or writing to Nessie")
    parser.add_argument("--population", choices=("auto", "owned", "enterprise"), default="auto")
    args = parser.parse_args()
    try:
        if args.probe:
            result = probe(os.getenv("NESSIE_API_KEY", ""), args.base_url or ORIGINS[0])
        else:
            result = ingest(os.getenv("NESSIE_API_KEY", ""), args.cache_dir,
                            base_url=args.base_url, count=args.count, customer_ids=args.customer_id,
                            population=args.population, offset=args.offset)
        print(json.dumps(result, indent=2))
    except (IngestError, ValueError, OSError) as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
