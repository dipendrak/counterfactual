"""Fit the frozen core contract from raw cached records, without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from core.model import CashflowModel, Discretionary, Income, RecurringObligation
from data.cache import record_id
from data.money import monetary_unit, to_dollars, RETIRED_ACCOUNT_PREFIX, USD


class FitError(ValueError):
    """A snapshot cannot support an evidenced cashflow model."""


def _money(value, *, allow_negative: bool = False, unit: str = USD) -> float:
    try:
        amount = to_dollars(value, unit)
    except (TypeError, ValueError):
        raise FitError("A completed cashflow record has no valid amount") from None
    if not math.isfinite(amount) or (amount < 0 and not allow_negative):
        raise FitError("Amounts must be finite; historical cashflows must be nonnegative")
    return amount


def _day(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _cadence(rows: list[dict], *, strict: bool = True) -> int | None:
    if len(rows) < 3:
        return None
    days = sorted(r["day"] for r in rows)
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    median = statistics.median(gaps)
    amounts = [r["amount"] for r in rows]
    typical = statistics.median(amounts)
    if median <= 0 or typical <= 0 or min(gaps) <= 0:
        return None
    if strict and (any(abs(gap - median) > 4 for gap in gaps)
                   or any(abs(amount - typical) > typical * .15 for amount in amounts)):
        return None
    return max(1, round(median))


def _next_due(rows: list[dict], cadence: int, as_of: date) -> int:
    elapsed = (as_of - max(r["day"] for r in rows)).days
    # day zero is the cached balance's date; charges on that date have already posted.
    return cadence - elapsed % cadence


def _category(merchant: dict) -> str:
    categories = merchant.get("category") or ["other"]
    if isinstance(categories, str):
        categories = [categories]
    return str(categories[0]).strip().lower() or "other"


def fit(snapshot: dict, *, window_days: int = 365) -> CashflowModel:
    if window_days < 7:
        raise FitError("Fit window must contain at least a week")
    as_of = _day(snapshot.get("as_of"))
    if as_of is None:
        raise FitError("Snapshot needs a valid as_of date for deterministic fitting")
    cutoff = as_of - timedelta(days=window_days - 1)
    bundles = [b for b in snapshot["accounts"]
               if str(b["account"].get("type", "")).lower() in ("checking", "savings")
               and b.get("include_in_fit", True) is True
               and not str(b["account"].get("nickname", "")).startswith(RETIRED_ACCOUNT_PREFIX)]
    if not bundles:
        raise FitError("No cached checking or savings accounts")
    account_ids = {record_id(b["account"]) for b in bundles}
    merchants = {record_id(m): m for m in snapshot.get("merchants", [])}
    incomes: dict[str, list[dict]] = defaultdict(list)
    expenses: dict[str, list[dict]] = defaultdict(list)
    discretionary = []
    seen = set()

    def normalize(raw: dict, field: str, unit: str, *, bill: bool = False) -> dict | None:
        tid = record_id(raw)
        if tid in seen:
            return None
        # Scheduled/pending/cancelled records are not realized historical cashflows.
        status = str(raw.get("status", "")).lower()
        if status not in ("completed", "executed", "paid") and (status or bill):
            return None
        day = _day(raw.get(field))
        if day is None:
            raise FitError(f"Completed transaction {tid} has no valid {field}")
        if not cutoff <= day <= as_of:
            return None
        seen.add(tid)
        amount = raw.get("payment_amount") if bill and "amount" not in raw else raw.get("amount")
        return {"id": tid, "day": day, "amount": _money(amount, unit=unit), "raw": raw}

    for bundle in bundles:
        unit = monetary_unit(snapshot, bundle)
        for raw in bundle["deposits"]:
            row = normalize(raw, "transaction_date", unit)
            if row and row["amount"] > 0:
                source = str(raw.get("description") or "deposits").strip().casefold()
                incomes[source].append(row)
        for collection, field in (("purchases", "purchase_date"), ("bills", "payment_date")):
            for raw in bundle[collection]:
                row = normalize(raw, field, unit, bill=collection == "bills")
                if row is None or row["amount"] == 0:
                    continue
                mid = raw.get("merchant_id")
                # Nessie bills have payee rather than merchant_id. Keep an exact payee
                # grouping for these records; do not invent a merchant identifier.
                key = f"merchant:{mid}" if mid else f"payee:{str(raw.get('payee') or '').strip().casefold()}"
                if mid or raw.get("payee"):
                    expenses[key].append(row)
                else:
                    discretionary.append(row)
        for raw in bundle["withdrawals"]:
            row = normalize(raw, "transaction_date", unit)
            if row and row["amount"]:
                discretionary.append(row)
        for raw in bundle["transfers"]:
            # Ignore movements between this customer's cash accounts and incoming
            # transfers: the frozen income contract uses deposits.
            if raw.get("payer_id") not in account_ids or raw.get("payee_id") in account_ids:
                continue
            row = normalize(raw, "transaction_date", unit)
            if row and row["amount"]:
                discretionary.append(row)

    candidates = [(sum(r["amount"] for r in rows), source, rows)
                  for source, rows in incomes.items() if len(rows) >= 3]
    if not candidates:
        raise FitError("At least three posted deposits from an income source are needed")
    _, _, income_rows = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))
    # Irregular income: median observed gap and sample amount dispersion; never
    # fabricate a paycheck or discard irregular deposits from the dominant source.
    income_cadence = _cadence(income_rows, strict=False)
    if income_cadence is None:
        raise FitError("Dominant income source has no positive cadence")
    amounts = [r["amount"] for r in income_rows]
    income = Income(income_cadence, round(statistics.mean(amounts), 2),
                    round(statistics.pstdev(amounts), 2), _next_due(income_rows, income_cadence, as_of),
                    sorted(r["id"] for r in income_rows))

    recurring = []
    essential_categories = {"rent", "housing", "mortgage", "loan", "loans", "car payment", "student loan"}
    for key, rows in sorted(expenses.items()):
        cadence = _cadence(rows)
        if cadence is None:
            discretionary.extend(rows)
            continue
        raw = rows[0]["raw"]
        mid = raw.get("merchant_id")
        merchant = merchants.get(mid, {})
        label = str(merchant.get("name") or raw.get("payee") or raw.get("description") or mid or "Recurring payment")
        # Existing core extension. Never infer essential status for all insurance.
        essential = _category(merchant) in essential_categories
        if not mid:
            essential = label.strip().lower() in essential_categories
        recurring.append(RecurringObligation(
            "rec_" + hashlib.sha256(key.encode()).hexdigest()[:16], label, mid,
            round(statistics.mean(r["amount"] for r in rows), 2), cadence,
            _next_due(rows, cadence, as_of), sorted(r["id"] for r in rows), essential))

    # Include empty weeks. Dividing only by weeks with spending biases the fit upward.
    # The last bucket can be partial; normalize it to seven days before taking stats.
    n_weeks = math.ceil(window_days / 7)
    totals = [0.0] * n_weeks
    categories: dict[str, float] = defaultdict(float)
    for row in discretionary:
        bucket = (row["day"] - cutoff).days // 7
        totals[bucket] += row["amount"]
        categories[_category(merchants.get(row["raw"].get("merchant_id"), {}))] += row["amount"]
    if window_days % 7:
        totals[-1] *= 7 / (window_days % 7)
    total_spend = sum(categories.values())
    disc = Discretionary(round(statistics.mean(totals), 2), round(statistics.pstdev(totals), 2),
                         {k: v / total_spend for k, v in sorted(categories.items())} if total_spend else {},
                         sorted(r["id"] for r in discretionary))
    evidence = sorted(set(income.evidence + disc.evidence + [tid for r in recurring for tid in r.evidence]))
    model = CashflowModel(record_id(snapshot["customer"]),
                          round(sum(_money(b["account"].get("balance"), unit=monetary_unit(snapshot, b),
                                           allow_negative=True) for b in bundles), 2),
                          window_days, income, recurring, disc, evidence)
    # Exercise the same boundary as A; catches contract drift at the producer.
    return CashflowModel.from_dict(model.to_dict())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--window-days", type=int, default=365)
    args = parser.parse_args()
    print(json.dumps(fit(json.loads(args.snapshot.read_text()), window_days=args.window_days).to_dict(), indent=2))


if __name__ == "__main__":
    main()
