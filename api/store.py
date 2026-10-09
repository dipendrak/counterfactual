"""Durable, immutable replay records, including the exact fitted inputs."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.model import CashflowModel, DecisionDelta, SimResult
from data.money import USD, USD_CENTS


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL,
                model_json TEXT NOT NULL,
                delta_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS transactions (
                txn_id TEXT PRIMARY KEY, record_json TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS transaction_units (
                txn_id TEXT PRIMARY KEY, monetary_unit TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS run_units (
                run_id TEXT PRIMARY KEY, units_json TEXT NOT NULL
            )""")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, result: SimResult, model: CashflowModel, delta: DecisionDelta, response: dict,
             evidence: dict[str, dict], *, evidence_units: dict[str, str] | None = None) -> None:
        encode = lambda value: json.dumps(value, allow_nan=False, separators=(",", ":"))
        units = evidence_units if evidence_units is not None else {tid: USD for tid in evidence}
        if set(units) != set(evidence) or any(unit not in (USD, USD_CENTS) for unit in units.values()):
            raise ValueError("Evidence monetary units must be complete and known")
        with self.connect() as connection:
            for tid, raw in evidence.items():
                old = connection.execute("SELECT record_json FROM transactions WHERE txn_id = ?", (tid,)).fetchone()
                if old and (json.loads(old[0]) != raw or self._transaction_unit(connection, tid) != units[tid]):
                    raise ValueError("Archived transaction record or monetary unit conflicts")
            connection.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)", (
                result.run_id, datetime.now(timezone.utc).isoformat(), encode(result.to_dict()),
                encode(model.to_dict()), encode(delta.to_dict()), encode(response), encode(evidence)))
            connection.executemany("INSERT OR IGNORE INTO transactions VALUES (?, ?)",
                                   [(tid, encode(raw)) for tid, raw in evidence.items()])
            connection.executemany("INSERT OR IGNORE INTO transaction_units VALUES (?, ?)", units.items())
            connection.execute("INSERT INTO run_units VALUES (?, ?)", (
                result.run_id, encode({"model": USD, "delta": USD, "evidence": units})))

    @staticmethod
    def _transaction_unit(connection, txn_id: str) -> str:
        row = connection.execute("SELECT monetary_unit FROM transaction_units WHERE txn_id = ?", (txn_id,)).fetchone()
        return row[0] if row else USD  # Original saved runs used dollar records.

    def transaction_unit(self, txn_id: str) -> str:
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone():
                raise KeyError(txn_id)
            return self._transaction_unit(connection, txn_id)

    def transaction(self, txn_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT record_json FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
        if row is None:
            raise KeyError(txn_id)
        return json.loads(row[0])

    def get(self, run_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT result_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row[0])

    def inputs(self, run_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT model_json, delta_json, evidence_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            units = connection.execute("SELECT units_json FROM run_units WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        evidence = json.loads(row[2])
        return {"model": json.loads(row[0]), "delta": json.loads(row[1]), "evidence": evidence,
                "amount_units": json.loads(units[0]) if units else {
                    "model": USD, "delta": USD, "evidence": {tid: USD for tid in evidence}}}
