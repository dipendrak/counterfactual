"""Integration seams for Dev C; no Gemini prompts or SDK calls live here."""
from __future__ import annotations

import importlib
import math
import re
from dataclasses import dataclass
from typing import Callable

from core.model import Claim, DecisionDelta, SimResult
from core.run import run


class RephraseRequired(ValueError):
    pass


def explicit_parse(text: str) -> DecisionDelta:
    """Offline unblocking fake: only accepts complete, explicit forms, never guesses dates."""
    pattern = (
        r"(?:can i afford|spend)\s+\$(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)"
        r"(?P<cadence>/(?:month|mo|week|year))?\s+"
        r"(?:starting\s+)?(?P<date>today|in (?P<days>\d+) days)\??"
    )
    match = re.fullmatch(pattern, text.strip(), re.IGNORECASE)
    if not match:
        raise RephraseRequired("Please include an amount and timing, for example: Can I afford $1400 today? "
                               "Offline mode also supports $65/month starting today or in 30 days.")
    cadence = {"/month": 30, "/mo": 30, "/week": 7, "/year": 365}.get((match["cadence"] or "").lower())
    return DecisionDelta(kind="recurring" if cadence else "one_time",
                         amount=float(match["amount"].replace(",", "")),
                         start_day=int(match["days"] or 0), cadence_days=cadence,
                         label="Explicit expense", raw_text=text, confidence=1.0)


def parse(text: str, mode: str = "auto") -> DecisionDelta:
    if mode == "explicit":
        return explicit_parse(text)
    try:
        module = importlib.import_module("llm.parse")
    except ModuleNotFoundError as exc:
        if mode == "auto" and exc.name in ("llm", "llm.parse"):
            return explicit_parse(text)
        raise RephraseRequired("The decision parser is unavailable. Please try again when it is configured.") from None
    return module.parse(text)


def render(claims, culprit):
    return importlib.import_module("llm.render").render(claims, culprit)


def validate(prose, claims):
    return importlib.import_module("llm.validate").validate(prose, claims)


def validate_delta(delta: DecisionDelta | dict) -> DecisionDelta:
    if isinstance(delta, dict):
        delta = DecisionDelta.from_dict(delta)
    if not isinstance(delta, DecisionDelta):
        raise RephraseRequired("Please rephrase with a specific amount and date.")
    numbers = (delta.amount, delta.start_day, delta.confidence)
    if (not all(math.isfinite(x) for x in numbers) or delta.amount <= 0 or delta.start_day < 0
            or not 0.5 <= delta.confidence <= 1
            or (delta.cadence_days is not None and (not math.isfinite(delta.cadence_days) or delta.cadence_days <= 0))):
        raise RephraseRequired("Please rephrase with a positive amount and a clear date or recurrence.")
    if delta.kind == "one_time" and delta.cadence_days is not None:
        raise RephraseRequired("Please clarify whether this is a one-time or recurring expense.")
    return delta


def template(claims: list[Claim]) -> str:
    """Numbers and merchant names are formatted directly from typed claim values."""
    values = {claim.kind: claim.value for claim in claims}
    probability = float(values["breach_rate_with_delta"])
    baseline = float(values["baseline_breach_rate"])
    count = int(values["paths_simulated"])
    prose = (f"In {count} simulated futures, the chance of a balance below the breach threshold "
             f"with this expense is {probability:.2%}, compared with {baseline:.2%} without it.")
    if "culprit_label" in values:
        prose += (f" The largest contributing recurring charge selected by the simulation is "
                  f"{values['culprit_label']} (${float(values['culprit_amount']):.2f}); removing it reduces "
                  f"the breach rate by {float(values['culprit_marginal_contribution']) * 100:.2f} percentage points.")
    else:
        prose += " No recurring charge had a positive measured contribution to the breach rate."
    return prose + " See the replay for paths and transaction evidence."


def check_evidence(result: SimResult, resolve: Callable) -> None:
    if not result.claims:
        raise ValueError("Simulation emitted no evidenced claims")
    for claim in result.claims:
        if not claim.evidence:
            raise ValueError("Claim has no transaction evidence")
        for tid in claim.evidence:
            resolve(tid)


def checked_template(result: SimResult) -> str:
    """Fallback validation is construction-based; arbitrary prose never enters it."""
    values = {claim.kind: claim for claim in result.claims}
    expected = {"baseline_breach_rate": result.baseline_breach_rate,
                "breach_rate_with_delta": result.delta_breach_rate,
                "paths_simulated": result.n_paths}
    if result.culprit:
        expected.update(culprit_label=result.culprit.label, culprit_amount=result.culprit.amount,
                        culprit_marginal_contribution=result.culprit.marginal_contribution)
    elif "culprit_label" in values:
        raise ValueError("Unexpected culprit claim")
    for kind, value in expected.items():
        if kind not in values or values[kind].value != value:
            raise ValueError("Claim/result mismatch")
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise ValueError("Non-finite claim")
    return template(result.claims)


def validation_passed(value) -> bool:
    return (value.get("passed") if isinstance(value, dict) else getattr(value, "passed", None)) is True


@dataclass
class Pipeline:
    parser: Callable
    renderer: Callable = render
    validator: Callable = validate
    simulator: Callable = run
