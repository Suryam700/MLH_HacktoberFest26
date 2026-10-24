from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

from civicfix.models import CivicIssue


STOP_WORDS = {
    "a", "an", "the", "at", "near", "by", "on", "in", "is", "there", "this", "that",
    "to", "of", "for", "and", "with", "beside", "here", "road", "issue", "problem",
}


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2 and word not in STOP_WORDS}


def text_similarity(first: str, second: str) -> float:
    left, right = _tokens(first), _tokens(second)
    if not left or not right:
        return 0.0
    jaccard = len(left & right) / len(left | right)
    sequence = SequenceMatcher(None, " ".join(sorted(left)), " ".join(sorted(right))).ratio()
    return round(0.6 * jaccard + 0.4 * sequence, 3)


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def find_duplicate_candidate(
    issues: list[CivicIssue], *, category: str, description: str,
    latitude: float | None, longitude: float | None,
) -> tuple[str | None, float]:
    """Return a conservative duplicate candidate using category, words and location.

    This MVP intentionally avoids claiming semantic/vector search. Reports with conflicting
    categories are never merged, and borderline matches remain separate issues.
    """
    best_id, best_score = None, 0.0
    for issue in issues:
        if issue.category != category or issue.status == "Resolved":
            continue
        similarity = text_similarity(description, issue.summary + " " + issue.location_text)
        both_have_coordinates = None not in (latitude, longitude, issue.latitude, issue.longitude)
        if both_have_coordinates:
            distance = distance_meters(latitude, longitude, issue.latitude, issue.longitude)
            if distance > 120 or similarity < 0.18:
                continue
            score = 0.55 * similarity + 0.45 * (1 - distance / 120)
            threshold = 0.48
        else:
            if similarity < 0.68:
                continue
            score, threshold = similarity, 0.68
        if score >= threshold and score > best_score:
            best_id, best_score = issue.id, score
    return best_id, round(best_score, 3)
