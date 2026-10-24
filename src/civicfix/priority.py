from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone

from civicfix.models import ComplaintDraft


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scoring.json"


@lru_cache(maxsize=1)
def _weights() -> tuple[dict[str, float], float]:
    with CONFIG_PATH.open("r", encoding="utf-8") as stream:
        settings = json.load(stream)
    weights = settings["weights"]
    if set(weights) != {"severity", "public_impact", "safety_risk", "confidence", "freshness"}:
        raise ValueError("Priority config must define the five documented weights.")
    numeric_weights = {key: float(value) for key, value in weights.items()}
    if any(value < 0 for value in numeric_weights.values()) or abs(sum(numeric_weights.values()) - 1.0) > 0.001:
        raise ValueError("Priority weights must be non-negative and sum to 1.")
    decay_days = float(settings["freshness_decay_days"])
    if decay_days <= 0:
        raise ValueError("Freshness decay days must be greater than zero.")
    return numeric_weights, decay_days


def freshness_score(last_seen: str, now: datetime | None = None) -> float:
    """Linear freshness decay over the configured number of days, normalized to 0-100."""
    _, decay_days = _weights()
    now = now or datetime.now(timezone.utc)
    try:
        seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        age_days = max(0.0, (now - seen).total_seconds() / 86400)
    except (ValueError, TypeError):
        age_days = decay_days
    return max(0.0, 100.0 * (1.0 - age_days / decay_days))


def calculate_priority(draft: ComplaintDraft, *, last_seen: str | None = None) -> float:
    """Transparent prototype score; not an official policy or safety decision."""
    freshness = freshness_score(last_seen) if last_seen else 100.0
    return calculate_priority_values(
        severity=draft.severity, public_impact=draft.public_impact,
        safety_risk=draft.safety_risk, confidence=draft.confidence,
        freshness=freshness,
    )


def calculate_priority_values(*, severity: int, public_impact: int, safety_risk: int,
                              confidence: float, freshness: float) -> float:
    weights, _ = _weights()
    return round(
        weights["severity"] * severity
        + weights["public_impact"] * public_impact
        + weights["safety_risk"] * safety_risk
        + weights["confidence"] * confidence * 100
        + weights["freshness"] * freshness,
        1,
    )


def priority_label(score: float) -> str:
    if score >= 75:
        return "High"
    if score >= 45:
        return "Medium"
    return "Low"


def describe_formula() -> str:
    weights, decay_days = _weights()
    labels = {
        "severity": "severity", "public_impact": "public impact",
        "safety_risk": "safety risk", "confidence": "confidence",
        "freshness": "freshness",
    }
    parts = [f"{weights[key]:.0%} {labels[key]}" for key in weights]
    return " + ".join(parts) + f"; freshness decays over {decay_days:g} days"
