from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from civicfix.models import CivicIssue, ComplaintDraft, GovernmentResolution, ResolutionAssessment, SavedReport
from civicfix.priority import calculate_priority_values, freshness_score


DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "civicfix.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_path() -> Path:
    return Path(os.getenv("CIVICFIX_DB", str(DEFAULT_DB))).expanduser()


def _next_public_id(db: sqlite3.Connection) -> str:
    db.execute("UPDATE issue_id_sequence SET next_number=next_number+1 WHERE id=1")
    row = db.execute("SELECT next_number-1 AS number FROM issue_id_sequence WHERE id=1").fetchone()
    return f"CF-{int(row['number'])}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize() -> None:
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS issues (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, summary TEXT NOT NULL,
            public_id TEXT NOT NULL DEFAULT '', authority_status TEXT,
            location_text TEXT NOT NULL DEFAULT '', latitude REAL, longitude REAL,
            status TEXT NOT NULL DEFAULT 'Open', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            priority_score REAL NOT NULL DEFAULT 0, severity INTEGER NOT NULL DEFAULT 50,
            public_impact INTEGER NOT NULL DEFAULT 50, safety_risk INTEGER NOT NULL DEFAULT 50,
            evidence_confidence REAL NOT NULL DEFAULT 0.5, evidence_quality INTEGER NOT NULL DEFAULT 50,
            report_count INTEGER NOT NULL DEFAULT 1,
            confirmation_count INTEGER NOT NULL DEFAULT 0, before_photo BLOB, latest_photo BLOB,
            after_photo BLOB, resolution_status TEXT, resolution_confidence REAL,
            resolution_note TEXT NOT NULL DEFAULT '', resolution_updated_at TEXT,
            status_source TEXT NOT NULL DEFAULT 'evidence', status_updated_at TEXT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('Draft','Ready to submit','Submitted','In progress','Resolved')),
            external_reference TEXT NOT NULL DEFAULT '', citizen_description TEXT NOT NULL,
            location_text TEXT NOT NULL DEFAULT '', latitude REAL, longitude REAL,
            photo_jpeg BLOB, draft_json TEXT NOT NULL, issue_id TEXT,
            owner_session_key TEXT NOT NULL DEFAULT ''
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(reports)").fetchall()}
        if "issue_id" not in columns:  # Migrate reports saved by the first CivicFix version.
            db.execute("ALTER TABLE reports ADD COLUMN issue_id TEXT")
        if "owner_session_key" not in columns:
            db.execute("ALTER TABLE reports ADD COLUMN owner_session_key TEXT NOT NULL DEFAULT ''")
        issue_columns = {row["name"] for row in db.execute("PRAGMA table_info(issues)").fetchall()}
        for name, declaration in {
            "severity": "INTEGER NOT NULL DEFAULT 50",
            "public_impact": "INTEGER NOT NULL DEFAULT 50",
            "safety_risk": "INTEGER NOT NULL DEFAULT 50",
            "evidence_confidence": "REAL NOT NULL DEFAULT 0.5",
            "evidence_quality": "INTEGER NOT NULL DEFAULT 50",
        }.items():
            if name not in issue_columns:
                db.execute(f"ALTER TABLE issues ADD COLUMN {name} {declaration}")
        if "resolution_updated_at" not in issue_columns:
            db.execute("ALTER TABLE issues ADD COLUMN resolution_updated_at TEXT")
        if "status_source" not in issue_columns:
            db.execute("ALTER TABLE issues ADD COLUMN status_source TEXT NOT NULL DEFAULT 'evidence'")
        if "status_updated_at" not in issue_columns:
            db.execute("ALTER TABLE issues ADD COLUMN status_updated_at TEXT")
        if "public_id" not in issue_columns:
            db.execute("ALTER TABLE issues ADD COLUMN public_id TEXT NOT NULL DEFAULT ''")
        if "authority_status" not in issue_columns:
            db.execute("ALTER TABLE issues ADD COLUMN authority_status TEXT")
        # Assign a stable public CivicFix number to older UUID-backed issues.
        rows_without_public_id = db.execute("SELECT id FROM issues WHERE public_id='' OR public_id IS NULL ORDER BY first_seen, id").fetchall()
        next_public_number = 1001
        for row in rows_without_public_id:
            db.execute("UPDATE issues SET public_id=? WHERE id=?", (f"CF-{next_public_number}", row["id"]))
            next_public_number += 1
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_public_id ON issues(public_id)")
        db.execute("CREATE TABLE IF NOT EXISTS issue_id_sequence (id INTEGER PRIMARY KEY CHECK(id=1), next_number INTEGER NOT NULL)")
        max_id = db.execute("SELECT MAX(CAST(SUBSTR(public_id, 4) AS INTEGER)) AS number FROM issues WHERE public_id LIKE 'CF-%'").fetchone()
        db.execute("INSERT OR IGNORE INTO issue_id_sequence (id, next_number) VALUES (1, ?)",
                   (max(1001, int(max_id["number"] or 1000) + 1),))
        db.execute("""CREATE TABLE IF NOT EXISTS confirmations (
            issue_id TEXT NOT NULL, session_key TEXT NOT NULL,
            confirmation_type TEXT NOT NULL DEFAULT 'observed', created_at TEXT NOT NULL,
            PRIMARY KEY(issue_id, session_key)
        )""")
        confirmation_columns = {row["name"] for row in db.execute("PRAGMA table_info(confirmations)").fetchall()}
        if "confirmation_type" not in confirmation_columns:
            db.execute("ALTER TABLE confirmations ADD COLUMN confirmation_type TEXT NOT NULL DEFAULT 'observed'")
        db.execute("""CREATE TABLE IF NOT EXISTS model_runs (
            id TEXT PRIMARY KEY, model_name TEXT NOT NULL, task TEXT NOT NULL,
            prompt_version TEXT NOT NULL, created_at TEXT NOT NULL,
            validation_status TEXT NOT NULL, result_json TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS government_resolutions (
            id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, department TEXT NOT NULL,
            authority TEXT NOT NULL, status TEXT NOT NULL, resolution_notes TEXT NOT NULL,
            resolution_date TEXT NOT NULL, work_order_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, verification_json TEXT,
            FOREIGN KEY(issue_id) REFERENCES issues(id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS resolution_evidence (
            id TEXT PRIMARY KEY, resolution_id TEXT NOT NULL, photo_jpeg BLOB NOT NULL,
            created_at TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(resolution_id) REFERENCES government_resolutions(id) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_resolutions_issue ON government_resolutions(issue_id, created_at DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS issue_aliases (
            public_id TEXT PRIMARY KEY, merged_into_id TEXT NOT NULL,
            created_at TEXT NOT NULL, FOREIGN KEY(merged_into_id) REFERENCES issues(id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_reports_updated ON reports(updated_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority_score DESC)")

        # Preserve pre-upgrade reports as individual issue records instead of hiding them.
        legacy = db.execute("SELECT * FROM reports WHERE issue_id IS NULL").fetchall()
        for row in legacy:
            try:
                draft = ComplaintDraft.model_validate_json(row["draft_json"])
            except Exception:
                continue
            issue_id = str(uuid.uuid4())
            score = (0.35 * draft.severity + 0.25 * draft.public_impact
                     + 0.20 * draft.safety_risk + 0.10 * draft.confidence * 100 + 10)
            public_id = _next_public_id(db)
            db.execute("""INSERT INTO issues (
                id, public_id, category, summary, location_text, latitude, longitude, status,
                first_seen, last_seen, priority_score, severity, public_impact, safety_risk,
                evidence_confidence, evidence_quality, before_photo, latest_photo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (issue_id, public_id, draft.category, draft.summary, row["location_text"], row["latitude"],
                 row["longitude"], row["created_at"], row["updated_at"], score, draft.severity,
                 draft.public_impact, draft.safety_risk, draft.confidence, draft.evidence_quality,
                 row["photo_jpeg"], row["photo_jpeg"]),
            )
            db.execute("UPDATE reports SET issue_id=? WHERE id=?", (issue_id, row["id"]))


def _report_from_row(row: sqlite3.Row) -> SavedReport:
    return SavedReport(
        id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"],
        status=row["status"], external_reference=row["external_reference"],
        citizen_description=row["citizen_description"], location_text=row["location_text"],
        latitude=row["latitude"], longitude=row["longitude"], photo_jpeg=row["photo_jpeg"],
        draft=ComplaintDraft.model_validate_json(row["draft_json"]), issue_id=row["issue_id"],
        owner_session_key=row["owner_session_key"],
    )


def _issue_from_row(row: sqlite3.Row) -> CivicIssue:
    issue = CivicIssue(**dict(row))
    current_score = calculate_priority_values(
        severity=issue.severity, public_impact=issue.public_impact,
        safety_risk=issue.safety_risk, confidence=issue.evidence_confidence,
        freshness=freshness_score(issue.last_seen),
    )
    return issue.model_copy(update={"priority_score": current_score})


def save_report(
    *, report_id: str | None, status: str, external_reference: str,
    citizen_description: str, location_text: str, latitude: float | None,
    longitude: float | None, photo_jpeg: bytes | None, draft: ComplaintDraft,
    issue_id: str | None = None, priority_score: float = 0,
    owner_session_key: str = "",
) -> SavedReport:
    now = _now()
    is_new = report_id is None
    report_id = report_id or str(uuid.uuid4())
    with connect() as db:
        existing = db.execute("SELECT created_at, issue_id FROM reports WHERE id=?", (report_id,)).fetchone()
        created_at = existing["created_at"] if existing else now
        if existing:
            issue_id = existing["issue_id"] or issue_id or str(uuid.uuid4())
        else:
            issue_id = issue_id or str(uuid.uuid4())

        issue = db.execute("SELECT id FROM issues WHERE id=?", (issue_id,)).fetchone()
        if issue is None:
            public_id = _next_public_id(db)
            db.execute("""INSERT INTO issues (
                id, public_id, category, summary, location_text, latitude, longitude, status,
                first_seen, last_seen, priority_score, severity, public_impact, safety_risk,
                evidence_confidence, evidence_quality, report_count, before_photo, latest_photo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (issue_id, public_id, draft.category, draft.summary, location_text.strip(), latitude,
                 longitude, created_at, now, priority_score, draft.severity, draft.public_impact,
                 draft.safety_risk, draft.confidence, draft.evidence_quality, photo_jpeg, photo_jpeg),
            )
        elif is_new:
            db.execute("""UPDATE issues SET last_seen=?, report_count=report_count+1,
                priority_score=?, latest_photo=COALESCE(?, latest_photo),
                before_photo=COALESCE(before_photo, ?),
                severity=?, public_impact=?, safety_risk=?, evidence_confidence=?, evidence_quality=?,
                location_text=CASE WHEN location_text='' THEN ? ELSE location_text END,
                latitude=COALESCE(latitude, ?), longitude=COALESCE(longitude, ?)
                WHERE id=?""",
                (now, priority_score, photo_jpeg, photo_jpeg, draft.severity, draft.public_impact, draft.safety_risk,
                 draft.confidence, draft.evidence_quality, location_text.strip(), latitude, longitude, issue_id),
            )
        db.execute("""INSERT INTO reports (
            id, created_at, updated_at, status, external_reference, citizen_description,
            location_text, latitude, longitude, photo_jpeg, draft_json, issue_id, owner_session_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,
            status=excluded.status, external_reference=excluded.external_reference,
            citizen_description=excluded.citizen_description, location_text=excluded.location_text,
            latitude=excluded.latitude, longitude=excluded.longitude,
            photo_jpeg=COALESCE(excluded.photo_jpeg, reports.photo_jpeg), draft_json=excluded.draft_json,
            owner_session_key=COALESCE(NULLIF(excluded.owner_session_key, ''), reports.owner_session_key)""",
            (report_id, created_at, now, status, external_reference.strip(), citizen_description.strip(),
             location_text.strip(), latitude, longitude, photo_jpeg, draft.model_dump_json(), issue_id,
             owner_session_key),
        )
        row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    return _report_from_row(row)


def list_reports(owner_session_key: str | None = None) -> list[SavedReport]:
    with connect() as db:
        if owner_session_key is None:
            rows = db.execute("SELECT * FROM reports ORDER BY updated_at DESC").fetchall()
        else:
            rows = db.execute("SELECT * FROM reports WHERE owner_session_key=? ORDER BY updated_at DESC",
                              (owner_session_key,)).fetchall()
    return [_report_from_row(row) for row in rows]


def list_issues() -> list[CivicIssue]:
    with connect() as db:
        rows = db.execute("SELECT * FROM issues").fetchall()
    issues = [_issue_from_row(row) for row in rows]
    return sorted(issues, key=lambda issue: (issue.priority_score, issue.last_seen), reverse=True)


def get_issue(issue_id: str) -> CivicIssue | None:
    with connect() as db:
        row = db.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    return _issue_from_row(row) if row else None


def get_issue_by_public_id(public_id: str) -> CivicIssue | None:
    with connect() as db:
        key = public_id.strip().upper()
        row = db.execute("SELECT * FROM issues WHERE UPPER(public_id)=?", (key,)).fetchone()
        if row is None:
            row = db.execute("""SELECT issues.* FROM issues JOIN issue_aliases
                ON issues.id=issue_aliases.merged_into_id WHERE UPPER(issue_aliases.public_id)=?""", (key,)).fetchone()
    return _issue_from_row(row) if row else None


def list_reports_for_issue(issue_id: str) -> list[SavedReport]:
    with connect() as db:
        rows = db.execute("SELECT * FROM reports WHERE issue_id=? ORDER BY created_at DESC", (issue_id,)).fetchall()
    return [_report_from_row(row) for row in rows]


def confirm_issue(issue_id: str, session_key: str, confirmation_type: str = "observed") -> bool:
    if confirmation_type not in {"observed", "still_present", "fixed", "unsure", "same_issue", "different_issue"}:
        raise ValueError("Unsupported confirmation type.")
    with connect() as db:
        inserted = db.execute("""INSERT OR IGNORE INTO confirmations
            (issue_id, session_key, confirmation_type, created_at) VALUES (?, ?, ?, ?)""",
                              (issue_id, session_key, confirmation_type, _now())).rowcount
        if inserted:
            db.execute("UPDATE issues SET confirmation_count=confirmation_count+1 WHERE id=?", (issue_id,))
    return bool(inserted)


def confirmation_counts(issue_id: str) -> dict[str, int]:
    with connect() as db:
        rows = db.execute("SELECT confirmation_type, COUNT(*) AS total FROM confirmations WHERE issue_id=? GROUP BY confirmation_type", (issue_id,)).fetchall()
    return {row["confirmation_type"]: row["total"] for row in rows}


def count_confirmations_for_session(session_key: str) -> int:
    with connect() as db:
        row = db.execute("SELECT COUNT(*) AS total FROM confirmations WHERE session_key=?", (session_key,)).fetchone()
    return int(row["total"])


def issue_timeline(issue_id: str) -> list[dict[str, str]]:
    with connect() as db:
        reports = db.execute("SELECT created_at FROM reports WHERE issue_id=?", (issue_id,)).fetchall()
        confirmations = db.execute("SELECT created_at, confirmation_type FROM confirmations WHERE issue_id=?", (issue_id,)).fetchall()
        issue = db.execute("SELECT resolution_status, resolution_updated_at, status_source, status_updated_at, status FROM issues WHERE id=?", (issue_id,)).fetchone()
        authority_records = db.execute("SELECT created_at, status, department FROM government_resolutions WHERE issue_id=? ORDER BY created_at", (issue_id,)).fetchall()
    events = [{"at": row["created_at"], "event": "Citizen report"} for row in reports]
    labels = {
        "observed": "Community observation", "still_present": "Community confirmed issue still present",
        "fixed": "Community reported issue fixed", "unsure": "Community unsure",
        "same_issue": "Confirmed same issue", "different_issue": "Marked as different issue",
    }
    events.extend({"at": row["created_at"], "event": labels.get(row["confirmation_type"], "Community confirmation")}
                  for row in confirmations)
    if issue and issue["resolution_updated_at"]:
        events.append({"at": issue["resolution_updated_at"],
                       "event": f"AI visual assessment: {issue['resolution_status']}"})
    if issue and issue["status_source"] == "reviewer" and issue["status_updated_at"]:
        events.append({"at": issue["status_updated_at"], "event": f"Reviewer set status: {issue['status']}"})
    if issue and issue["status_source"] == "government" and issue["status_updated_at"] and issue["status"] == "In progress":
        events.append({"at": issue["status_updated_at"], "event": "Authority marked work in progress"})
    events.extend({"at": row["created_at"], "event": f"Authority submitted {row['status'].lower()} evidence · {row['department']}"}
                  for row in authority_records)
    return sorted(events, key=lambda event: event["at"])


def merge_issues(source_id: str, target_id: str) -> None:
    if source_id == target_id:
        raise ValueError("Choose two different issues to merge.")
    with connect() as db:
        source = db.execute("SELECT * FROM issues WHERE id=?", (source_id,)).fetchone()
        target = db.execute("SELECT * FROM issues WHERE id=?", (target_id,)).fetchone()
        if source is None or target is None:
            raise ValueError("Issue not found.")
        if source["public_id"]:
            db.execute("INSERT OR REPLACE INTO issue_aliases (public_id, merged_into_id, created_at) VALUES (?, ?, ?)",
                       (source["public_id"], target_id, _now()))
        db.execute("UPDATE issue_aliases SET merged_into_id=? WHERE merged_into_id=?", (target_id, source_id))
        db.execute("UPDATE government_resolutions SET issue_id=? WHERE issue_id=?", (target_id, source_id))
        db.execute("UPDATE reports SET issue_id=? WHERE issue_id=?", (target_id, source_id))
        db.execute("""INSERT OR IGNORE INTO confirmations
            (issue_id, session_key, confirmation_type, created_at)
            SELECT ?, session_key, confirmation_type, created_at FROM confirmations WHERE issue_id=?""",
            (target_id, source_id),
        )
        db.execute("DELETE FROM confirmations WHERE issue_id=?", (source_id,))
        db.execute("""UPDATE issues SET report_count=report_count+?,
            confirmation_count=(SELECT COUNT(*) FROM confirmations WHERE issue_id=?),
            first_seen=MIN(first_seen, ?), last_seen=MAX(last_seen, ?),
            before_photo=COALESCE(before_photo, ?), latest_photo=COALESCE(?, latest_photo)
            WHERE id=?""",
            (source["report_count"], target_id, source["first_seen"], source["last_seen"],
             source["before_photo"], source["latest_photo"], target_id),
        )
        db.execute("DELETE FROM issues WHERE id=?", (source_id,))


def update_issue_status(issue_id: str, status: str) -> None:
    allowed = {"Open", "In progress", "Partially resolved", "Resolved", "Uncertain"}
    if status not in allowed:
        raise ValueError("Unsupported issue status.")
    with connect() as db:
        db.execute("UPDATE issues SET status=?, status_source='reviewer', status_updated_at=? WHERE id=?",
                   (status, _now(), issue_id))


def save_resolution(issue_id: str, after_photo: bytes, assessment: ResolutionAssessment) -> None:
    status = {
        "resolved": "Resolved", "partially_resolved": "Partially resolved",
        "still_open": "Open", "uncertain": "Uncertain",
    }[assessment.status]
    note = assessment.reasoning_summary
    if assessment.visible_changes:
        note += "\nVisible changes: " + "; ".join(assessment.visible_changes)
    if assessment.limitations:
        note += "\nLimitations: " + "; ".join(assessment.limitations)
    with connect() as db:
        db.execute("""UPDATE issues SET after_photo=?,
            status=CASE WHEN authority_status IS NULL THEN ? ELSE status END,
            status_source=CASE WHEN authority_status IS NULL THEN 'ai_assessment' ELSE status_source END,
            resolution_status=?, resolution_confidence=?, resolution_note=?,
            resolution_updated_at=?, status_updated_at=CASE WHEN authority_status IS NULL THEN ? ELSE status_updated_at END
            WHERE id=?""",
            (after_photo, status, assessment.status, assessment.confidence, note, _now(), _now(), issue_id),
        )


def start_authority_work(issue_id: str) -> None:
    with connect() as db:
        result = db.execute("UPDATE issues SET status='In progress', authority_status='In progress', status_source='government', status_updated_at=? WHERE id=?",
                            (_now(), issue_id))
        if not result.rowcount:
            raise ValueError("Issue not found.")


def submit_government_resolution(
    *, issue_id: str, department: str, authority: str, status: str,
    resolution_notes: str, resolution_date: str, work_order_id: str,
    evidence_photos: list[bytes],
) -> str:
    allowed = {"Resolved", "Partially resolved", "Still open", "Uncertain"}
    if status not in allowed:
        raise ValueError("Unsupported authority resolution status.")
    if not department.strip() or not authority.strip() or not resolution_notes.strip():
        raise ValueError("Department, authority and resolution notes are required.")
    now = _now()
    resolution_id = str(uuid.uuid4())
    issue_status = {"Resolved": "Resolved", "Partially resolved": "Partially resolved",
                    "Still open": "Open", "Uncertain": "Uncertain"}[status]
    with connect() as db:
        if db.execute("SELECT 1 FROM issues WHERE id=?", (issue_id,)).fetchone() is None:
            raise ValueError("Issue not found.")
        db.execute("""INSERT INTO government_resolutions
            (id, issue_id, department, authority, status, resolution_notes, resolution_date, work_order_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (resolution_id, issue_id, department.strip(), authority.strip(), status,
             resolution_notes.strip(), resolution_date, work_order_id.strip(), now))
        for photo in evidence_photos:
            db.execute("INSERT INTO resolution_evidence (id, resolution_id, photo_jpeg, created_at) VALUES (?, ?, ?, ?)",
                       (str(uuid.uuid4()), resolution_id, photo, now))
        db.execute("UPDATE issues SET status=?, authority_status=?, status_source='government', status_updated_at=? WHERE id=?",
                   (issue_status, status, now, issue_id))
    return resolution_id


def get_government_resolutions(issue_id: str | None = None) -> list[GovernmentResolution]:
    with connect() as db:
        if issue_id:
            rows = db.execute("SELECT * FROM government_resolutions WHERE issue_id=? ORDER BY created_at DESC", (issue_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM government_resolutions ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            photos = db.execute("SELECT photo_jpeg FROM resolution_evidence WHERE resolution_id=? ORDER BY created_at, id", (row["id"],)).fetchall()
            verification = ResolutionAssessment.model_validate_json(row["verification_json"]) if row["verification_json"] else None
            result.append(GovernmentResolution(
                id=row["id"], issue_id=row["issue_id"], department=row["department"],
                authority=row["authority"], status=row["status"], resolution_notes=row["resolution_notes"],
                resolution_date=row["resolution_date"], work_order_id=row["work_order_id"],
                created_at=row["created_at"], verification=verification,
                evidence_photos=[photo["photo_jpeg"] for photo in photos],
            ))
    return result


def save_government_verification(resolution_id: str, assessment: ResolutionAssessment) -> None:
    with connect() as db:
        row = db.execute("SELECT issue_id FROM government_resolutions WHERE id=?", (resolution_id,)).fetchone()
        if row is None:
            raise ValueError("Government resolution not found.")
        photo = db.execute("SELECT photo_jpeg FROM resolution_evidence WHERE resolution_id=? ORDER BY created_at, id LIMIT 1", (resolution_id,)).fetchone()
        db.execute("UPDATE government_resolutions SET verification_json=? WHERE id=?",
                   (assessment.model_dump_json(), resolution_id))
        note = assessment.reasoning_summary
        if assessment.limitations:
            note += "\nLimitations: " + "; ".join(assessment.limitations)
        db.execute("""UPDATE issues SET after_photo=COALESCE(?, after_photo), resolution_status=?,
            resolution_confidence=?, resolution_note=?, resolution_updated_at=? WHERE id=?""",
            (photo["photo_jpeg"] if photo else None, assessment.status, assessment.confidence,
             note, _now(), row["issue_id"]))


def record_model_run(*, model_name: str, task: str, validation_status: str, result: dict) -> None:
    with connect() as db:
        db.execute("INSERT INTO model_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (str(uuid.uuid4()), model_name, task, "civicfix-v2", _now(),
                    validation_status, json.dumps(result, ensure_ascii=False)))
