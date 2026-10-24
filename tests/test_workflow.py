from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from civicfix.ai import local_fallback
from civicfix.deduplication import find_duplicate_candidate
from civicfix.models import CivicIssue, ComplaintDraft, ResolutionAssessment
from civicfix.priority import calculate_priority, priority_label
from civicfix.storage import (
    confirmation_counts, confirm_issue, get_government_resolutions, get_issue,
    get_issue_by_public_id, initialize, issue_timeline,
    list_issues, list_reports, list_reports_for_issue, merge_issues, save_report,
    save_government_verification, save_resolution, start_authority_work,
    submit_government_resolution, update_issue_status,
)


def draft(category: str = "Pothole / damaged road", summary: str = "Large pothole near bus stop") -> ComplaintDraft:
    return ComplaintDraft(
        is_civic_issue=True, validation_status="valid", confidence=.9,
        category=category, summary=summary,
        draft_complaint=f"Please inspect the reported issue: {summary}.",
        severity=80, public_impact=70, safety_risk=75, evidence_quality=85,
        suggested_department="Unverified road maintenance", needs_clarification=False,
        reasoning_summary="Visible road damage is described by the resident.",
        evidence_observations=["Visible road damage"], missing_information=[],
        follow_up_questions=[], priority="Urgent",
    )


class PriorityAndValidationTests(unittest.TestCase):
    def test_priority_uses_configured_factors_and_band(self) -> None:
        score = calculate_priority(draft())
        self.assertGreaterEqual(score, 70)
        self.assertEqual(priority_label(score), "High")

    def test_template_fallback_is_not_misrepresented_as_validated_ai(self) -> None:
        result = local_fallback("A pothole is outside the bus stop", "Main road")
        self.assertEqual(result.validation_status, "ambiguous")
        self.assertFalse(result.is_civic_issue)
        self.assertTrue(result.needs_clarification)

    def test_duplicate_match_requires_category_and_location_or_strong_text(self) -> None:
        sample = CivicIssue(
            id="issue-1", category="Pothole / damaged road", summary="Large pothole near bus stop",
            location_text="Main street", latitude=26.45, longitude=80.33,
            first_seen="2026-10-20T10:00:00+00:00", last_seen="2026-10-24T10:00:00+00:00",
        )
        candidate, _ = find_duplicate_candidate(
            [sample], category="Pothole / damaged road",
            description="Large pothole near the bus stop", latitude=26.4501, longitude=80.3301,
        )
        self.assertEqual(candidate, "issue-1")
        candidate, _ = find_duplicate_candidate(
            [sample], category="Garbage / sanitation",
            description="Large pothole near the bus stop", latitude=26.4501, longitude=80.3301,
        )
        self.assertIsNone(candidate)


class StorageWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "civicfix.sqlite3")
        self.env = patch.dict(os.environ, {"CIVICFIX_DB": self.db_path})
        self.env.start()
        initialize()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp_dir.cleanup()

    def _save(self, *, category: str = "Pothole / damaged road", issue_id: str | None = None,
              description: str = "A pothole is visible near the bus stop"):
        current = draft(category=category, summary=description)
        return save_report(
            report_id=None, status="Ready to submit", external_reference="",
            citizen_description=description, location_text="Main street", latitude=26.45,
            longitude=80.33, photo_jpeg=b"jpeg-bytes", draft=current,
            issue_id=issue_id, priority_score=calculate_priority(current),
            owner_session_key="test-session",
        )

    def test_report_cluster_confirmation_resolution_and_timeline(self) -> None:
        first = self._save()
        second = self._save(issue_id=first.issue_id, description="Another observation of the pothole")
        self.assertEqual(first.issue_id, second.issue_id)
        self.assertEqual(get_issue(first.issue_id).report_count, 2)
        self.assertEqual(len(list_reports(owner_session_key="test-session")), 2)
        self.assertTrue(confirm_issue(first.issue_id, "voter-1", "still_present"))
        self.assertFalse(confirm_issue(first.issue_id, "voter-1", "fixed"))
        self.assertEqual(confirmation_counts(first.issue_id)["still_present"], 1)

        assessment = ResolutionAssessment(
            status="uncertain", confidence=.5, visible_changes=["A later image was added"],
            reasoning_summary="The evidence is inconclusive.", limitations=["The camera angle differs."],
        )
        save_resolution(first.issue_id, b"after-image", assessment)
        issue = get_issue(first.issue_id)
        self.assertEqual(issue.status, "Uncertain")
        self.assertIn("Limitations:", issue.resolution_note)
        self.assertTrue(any(event["event"].startswith("AI visual assessment") for event in issue_timeline(first.issue_id)))

        third = self._save(category="Open drain", description="An uncovered drain on Main Street")
        merged_public_id = get_issue(third.issue_id).public_id
        merge_issues(third.issue_id, first.issue_id)
        self.assertEqual(len(list_reports_for_issue(first.issue_id)), 3)
        self.assertIsNone(get_issue(third.issue_id))
        self.assertEqual(get_issue_by_public_id(merged_public_id).id, first.issue_id)

        update_issue_status(first.issue_id, "Resolved")
        self.assertEqual(get_issue(first.issue_id).status, "Resolved")
        self.assertTrue(any("Reviewer set status" in event["event"] for event in issue_timeline(first.issue_id)))
        self.assertEqual(len(list_issues()), 1)

    def test_public_issue_ids_and_government_resolution_keep_statuses_separate(self) -> None:
        first = self._save()
        issue = get_issue(first.issue_id)
        self.assertEqual(issue.public_id, "CF-1001")
        self.assertEqual(get_issue_by_public_id("cf-1001").id, first.issue_id)

        start_authority_work(first.issue_id)
        self.assertEqual(get_issue(first.issue_id).status, "In progress")
        resolution_id = submit_government_resolution(
            issue_id=first.issue_id, department="Road Maintenance",
            authority="Prototype Authority", status="Resolved",
            resolution_notes="Surface repaired.", resolution_date="2026-10-24",
            work_order_id="WO-123", evidence_photos=[b"after-one", b"after-two"],
        )
        self.assertEqual(get_issue(first.issue_id).status, "Resolved")
        self.assertEqual(get_issue(first.issue_id).authority_status, "Resolved")
        self.assertIsNone(get_issue(first.issue_id).resolution_status)

        assessment = ResolutionAssessment(
            status="uncertain", confidence=.42, same_location_likelihood=.31,
            issue_persistence=.6, evidence_quality=.4, visible_changes=[],
            reasoning_summary="The after photos do not show the same area clearly.",
            limitations=["Location match is uncertain."],
        )
        save_government_verification(resolution_id, assessment)
        saved_resolution = get_government_resolutions(first.issue_id)[0]
        self.assertEqual(len(saved_resolution.evidence_photos), 2)
        self.assertEqual(saved_resolution.verification.status, "uncertain")
        self.assertEqual(get_issue(first.issue_id).status, "Resolved")
        self.assertEqual(get_issue(first.issue_id).resolution_status, "uncertain")


if __name__ == "__main__":
    unittest.main()
