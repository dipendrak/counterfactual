"""Browser-sized view of a SimResult for Dev D's replay page.

SimResult itself is unchanged (frozen contract). This is a derived view: same summary fields,
`paths` cut to a few representative full paths, plus a p10/p50/p90 band over all paths.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from core.model import SimResult

USD_DP = 2
DEFAULT_REPRESENTATIVE = 20


def representative_indices(min_balances: np.ndarray, k: int) -> list[int]:
    """k paths spread evenly across the min-balance ranking, always including the worst and best."""
    n = len(min_balances)
    if k >= n:
        return list(range(n))
    order = np.argsort(min_balances, kind="stable")
    picks = np.unique(np.round(np.linspace(0, n - 1, k)).astype(int))
    return sorted(int(order[p]) for p in picks)


def band_payload(result: SimResult, n_representative: int = DEFAULT_REPRESENTATIVE) -> dict[str, Any]:
    d = result.to_dict()
    if not result.paths:
        d["band"] = {"p10": [], "p50": [], "p90": []}
        d["paths_total"] = 0
        return d

    balances = np.array([p.balances for p in result.paths])
    p10, p50, p90 = np.round(np.percentile(balances, [10, 50, 90], axis=0), USD_DP)
    mins = np.array([p.min_balance for p in result.paths])
    keep = representative_indices(mins, n_representative)

    d["paths"] = [result.paths[i].to_dict() for i in keep]
    d["paths_total"] = len(result.paths)
    # balances[w] indexing matches PathRecord: index 0 is the opening balance.
    d["band"] = {"p10": p10.tolist(), "p50": p50.tolist(), "p90": p90.tolist()}
    return d
