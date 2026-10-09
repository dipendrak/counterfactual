"""Raw snapshot persistence. Runtime never makes a Nessie request."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from data.money import monetary_unit

COLLECTIONS = ("purchases", "deposits", "bills", "withdrawals", "transfers", "loans")


def record_id(record: dict[str, Any]) -> str:
    value = record.get("_id")
    if not isinstance(value, str) or not value:
        raise ValueError("Nessie record is missing its original _id")
    return value


def cache_path(directory: Path, customer_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", customer_id):
        raise ValueError("Invalid customer ID")
    return directory / f"{customer_id}.json"


def write_snapshot(directory: Path, snapshot: dict[str, Any]) -> Path:
    path = cache_path(directory, record_id(snapshot["customer"]))
    # Validate before replacing a last-known-good snapshot.
    CacheRepository.from_snapshots([snapshot])
    payload = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, suffix=".tmp", delete=False) as f:
            temporary = f.name
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return path


class CacheRepository:
    """An immutable-in-practice in-memory view of local snapshots; restart after ingest."""

    def __init__(self, directory: Path):
        snapshots = []
        for path in sorted(directory.glob("*.json")):
            snapshot = json.loads(path.read_text())
            if path.name != cache_path(directory, record_id(snapshot["customer"])).name:
                raise ValueError(f"Cache filename/customer mismatch: {path.name}")
            snapshots.append(snapshot)
        self._load(snapshots)

    @classmethod
    def from_snapshots(cls, snapshots: list[dict[str, Any]]) -> CacheRepository:
        repo = cls.__new__(cls)
        repo._load(snapshots)
        return repo

    def _load(self, snapshots: list[dict[str, Any]]) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.transaction_units: dict[str, str] = {}
        for snapshot in snapshots:
            cid = record_id(snapshot["customer"])
            if cid in self.snapshots:
                raise ValueError(f"Duplicate cached customer: {cid}")
            self.snapshots[cid] = snapshot
            for bundle in snapshot["accounts"]:
                record_id(bundle["account"])
                unit = monetary_unit(snapshot, bundle)
                for collection in COLLECTIONS:
                    for record in bundle[collection]:
                        tid = record_id(record)
                        if tid in self.transactions and self.transactions[tid] != record:
                            raise ValueError(f"Conflicting raw records for transaction {tid}")
                        if tid in self.transaction_units and self.transaction_units[tid] != unit:
                            raise ValueError(f"Conflicting monetary units for transaction {tid}")
                        self.transactions[tid] = record
                        self.transaction_units[tid] = unit

    def customers(self) -> list[dict[str, str]]:
        return [
            {"customer_id": cid, "name": " ".join(
                str(s["customer"].get(k, "")).strip() for k in ("first_name", "last_name")
            ).strip() or cid}
            for cid, s in sorted(self.snapshots.items())
        ]

    def snapshot(self, customer_id: str) -> dict[str, Any]:
        return self.snapshots[customer_id]

    def transaction(self, txn_id: str) -> dict[str, Any]:
        return self.transactions[txn_id]

    def transaction_unit(self, txn_id: str) -> str:
        return self.transaction_units[txn_id]
