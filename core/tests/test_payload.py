from __future__ import annotations

import json

from core.payload import band_payload
from core.run import run


def test_band_has_one_point_per_balance(model, delta):
    r = run(model, delta)
    band = band_payload(r)["band"]
    n = r.horizon_weeks + 1
    assert len(band["p10"]) == len(band["p50"]) == len(band["p90"]) == n
    assert all(lo <= mid <= hi for lo, mid, hi in zip(band["p10"], band["p50"], band["p90"]))


def test_band_keeps_twenty_paths_including_worst_and_best(model, delta):
    r = run(model, delta)
    p = band_payload(r)
    kept_mins = [x["min_balance"] for x in p["paths"]]
    all_mins = [x.min_balance for x in r.paths]
    assert len(p["paths"]) == 20
    assert p["paths_total"] == r.n_paths
    assert min(kept_mins) == min(all_mins)
    assert max(kept_mins) == max(all_mins)


def test_band_payload_is_deterministic_and_small(model, delta):
    r = run(model, delta)
    a = json.dumps(band_payload(r), sort_keys=True)
    b = json.dumps(band_payload(run(model, delta)), sort_keys=True)
    assert a == b
    assert len(a) < len(json.dumps(r.to_dict())) / 5


def test_band_payload_leaves_summary_untouched(model, delta):
    r = run(model, delta)
    p = band_payload(r)
    full = r.to_dict()
    for key in ("run_id", "baseline_breach_rate", "delta_breach_rate", "culprit", "claims", "ranked_drivers"):
        assert p[key] == full[key]


def test_band_payload_small_run_keeps_all_paths(model, delta):
    r = run(model, delta, n_paths=10)
    assert len(band_payload(r)["paths"]) == 10
