"""Design a demo customer whose villain is found by the sim, not fixed by construction.

Generates a Nessie-shaped snapshot (same shape as data/demo.py), fits it with Dev B's fitter,
and runs Dev A's core. Usage: python demo_profile.py [key=value ...] to override knobs.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from decimal import Decimal

from core.model import DecisionDelta
from core.run import run
from data.cache import COLLECTIONS
from data.fit import fit

AS_OF = date(2026, 10, 9)

KNOBS = dict(
    balance=1100.0, paycheck=1900, pay_offset=4,
    spending=260,
)
# (merchant_id, name, category, amount, cadence_days, first_offset_days_ago)
BILLS = [
    ("rent", "Rent", "housing", 1450, 30, 9),
    ("car", "Ally Auto", "loan", 315, 30, 21),
    ("geico", "Geico", "insurance", 168, 30, 15),
    ("tmobile", "T-Mobile", "utilities", 95, 30, 3),
    ("doordash", "DoorDash", "food delivery", 58, 7, 1),
    ("gym", "Planet Fitness", "fitness", 25, 30, 12),
]


def snapshot(k: dict) -> dict:
    cid, aid = "demo_jordan", "account_demo_jordan"
    bundle = {"account": {"_id": aid, "customer_id": cid, "type": "Checking", "balance": k["balance"]}}
    bundle.update({c: [] for c in COLLECTIONS})
    merchants = [{"_id": f"{m}_{cid}", "name": n, "category": [c]} for m, n, c, *_ in BILLS]
    merchants.append({"_id": f"grocer_{cid}", "name": "Harris Teeter", "category": ["food"]})
    elapsed, i = k["pay_offset"], 0
    while elapsed < 365:
        bundle["deposits"].append({"_id": f"{cid}_income_{i}", "payee_id": aid,
                                   "transaction_date": (AS_OF - timedelta(days=elapsed)).isoformat(),
                                   "amount": k["paycheck"] + (i % 3 - 1) * 60, "description": "Payroll",
                                   "status": "completed"})
        elapsed += 14
        i += 1
    for m, _, _, amount, cadence, offset in BILLS:
        for j, e in enumerate(range(offset, 365, cadence)):
            bundle["purchases"].append({"_id": f"{cid}_{m}_{j}", "payer_id": aid, "merchant_id": f"{m}_{cid}",
                                        "purchase_date": (AS_OF - timedelta(days=e)).isoformat(),
                                        "amount": float(Decimal(str(amount)) * Decimal([".97", "1.0", "1.03"][j % 3])) if cadence == 7 else amount,
                                        "status": "completed"})
    for j, e in enumerate(range(2, 365, 7)):
        bundle["purchases"].append({"_id": f"{cid}_spend_{j}", "payer_id": aid, "merchant_id": f"grocer_{cid}",
                                    "purchase_date": (AS_OF - timedelta(days=e)).isoformat(),
                                    "amount": float(Decimal(k["spending"]) * Decimal([".45", "1.4", ".9", "1.25"][j % 4])),
                                    "status": "completed"})
    return {"schema_version": 1, "source": "generated_fixture", "as_of": AS_OF.isoformat(),
            "customer": {"_id": cid, "first_name": "Jordan", "last_name": "(synthetic fixture)"},
            "accounts": [bundle], "merchants": merchants}


def report(k: dict, seeds=(1337, 1, 7, 42, 2026)) -> None:
    m = fit(snapshot(k))
    delta = DecisionDelta.from_dict(json.load(open("core/fixtures/delta_fixture.json")))
    print("fitted:", [(o.label, o.amount, o.cadence_days, o.essential) for o in m.recurring])
    print("income", m.income.mean, "/", m.income.cadence_days, "d | disc wk", m.discretionary.weekly_mean)
    for s in seeds:
        r = run(m, delta, seed=s)
        top = [(d.label, d.marginal_contribution) for d in r.ranked_drivers]
        print(f"seed {s:5}: base={r.baseline_breach_rate:.3f} delta={r.delta_breach_rate:.3f} "
              f"culprit={r.culprit.label if r.culprit else None} drivers={top}")


if __name__ == "__main__":
    k = dict(KNOBS)
    for a in sys.argv[1:]:
        key, v = a.split("=")
        k[key] = float(v)
    report(k)
