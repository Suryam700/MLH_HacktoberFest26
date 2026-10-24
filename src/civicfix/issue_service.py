from __future__ import annotations

from civicfix.deduplication import find_duplicate_candidate
from civicfix.models import ComplaintDraft
from civicfix.priority import calculate_priority
from civicfix.storage import list_issues, list_reports


def suggest_duplicate(draft: ComplaintDraft, description: str, location: str,
                      latitude: float | None, longitude: float | None) -> tuple[str | None, float]:
    reports_by_issue: dict[str, list[str]] = {}
    for report in list_reports():
        if report.issue_id:
            reports_by_issue.setdefault(report.issue_id, []).append(report.citizen_description)
    issues = [
        issue.model_copy(update={
            "summary": issue.summary + " " + " ".join(reports_by_issue.get(issue.id, []))
        })
        for issue in list_issues()
    ]
    return find_duplicate_candidate(
        issues, category=draft.category,
        description=f"{draft.summary} {description} {location}",
        latitude=latitude, longitude=longitude,
    )


def score_report(draft: ComplaintDraft) -> float:
    return calculate_priority(draft)


def report_quality(draft: ComplaintDraft, *, has_photo: bool, has_location: bool) -> int:
    completeness = 0.50 * draft.evidence_quality
    completeness += 20 if has_photo else 0
    completeness += 20 if has_location else 0
    completeness += 10 if not draft.missing_information else 0
    return round(completeness)
