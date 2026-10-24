from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from civicfix.models import CivicIssue, ComplaintDraft, ResolutionAssessment, SavedReport
from civicfix.priority import calculate_priority_values, freshness_score


DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "civicfix.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_path() -> Path:
    return Path(os.getenv("CIVICFIX_DB", str(DEFAULT_DB))).expanduser()


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize() -> None:
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS issues (
            id TEXT PRIMARY KEY, category TEXT NOT NULL, summary TEXT NOT NULL,
            location_text TEXT NOT NULL DEFAULT '', latitude REAL, longitude REAL,
            status TEXT NOT NULL DEFAULT 'Open', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            priority_score REAL NOT NULL DEFAULT 0, severity INTEGER NOT NULL DEFAULT 50,
            public_impact INTEGER NOT NULL DEFAULT 50, safety_risk INTEGER NOT NULL DEFAULT 50,
            evidence_confidence REAL NOT NULL DEFAULT 0.5, evidence_quality INTEGER NOT NULL DEFAULT 50,
            report_count INTEGER NOT NULL DEFAULT 1,
            confirmation_count INTEGER NOT NULL DEFAULT 0, before_photo BLOB, latest_photo BLOB,
            after_photo BLOB, resolution_status TEXT, resolution_confidence REAL,
            resolution_note TEXT NOT NULL DEFAULT ''
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('Draft','Ready to submit','Submitted','In progress','Resolved')),
            external_reference TEXT NOT NULL DEFAULT '', citizen_description TEXT NOT NULL,
            location_text TEXT NOT NULL DEFAULT '', latitude REAL, longitude REAL,
            photo_jpeg BLOB, draft_json TEXT NOT NULL, issue_id TEXT
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(reports)").fetchall()}
        if "issue_id" not in columns:  # Migrate reports saved by the first CivicFix version.
            db.execute("ALTER TABLE reports ADD COLUMN issue_id TEXT")
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
        db.execute("""CREATE TABLE IF NOT EXISTS confirmations (
            issue_id TEXT NOT NULL, session_key TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(issue_id, session_key)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS model_runs (
            id TEXT PRIMARY KEY, model_name TEXT NOT NULL, task TEXT NOT NULL,
            prompt_version TEXT NOT NULL, created_at TEXT NOT NULL,
            validation_status TEXT NOT NULL, result_json TEXT NOT NULL
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
            db.execute("""INSERT INTO issues (
                id, category, summary, location_text, latitude, longitude, status,
                first_seen, last_seen, priority_score, severity, public_impact, safety_risk,
                evidence_confidence, evidence_quality, before_photo, latest_photo
            ) VALUES (?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (issue_id, draft.category, draft.summary, row["location_text"], row["latitude"],
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
            db.execute("""INSERT INTO issues (
                id, category, summary, location_text, latitude, longitude, status,
                first_seen, last_seen, priority_score, severity, public_impact, safety_risk,
                evidence_confidence, evidence_quality, report_count, before_photo, latest_photo
            ) VALUES (?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (issue_id, draft.category, draft.summary, location_text.strip(), latitude,
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
            location_text, latitude, longitude, photo_jpeg, draft_json, issue_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,
            status=excluded.status, external_reference=excluded.external_reference,
            citizen_description=excluded.citizen_description, location_text=excluded.location_text,
            latitude=excluded.latitude, longitude=excluded.longitude,
            photo_jpeg=COALESCE(excluded.photo_jpeg, reports.photo_jpeg), draft_json=excluded.draft_json""",
            (report_id, created_at, now, status, external_reference.strip(), citizen_description.strip(),
             location_text.strip(), latitude, longitude, photo_jpeg, draft.model_dump_json(), issue_id),
        )
        row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    return _report_from_row(row)


def list_reports() -> list[SavedReport]:
    with connect() as db:
        rows = db.execute("SELECT * FROM reports ORDER BY updated_at DESC").fetchall()
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


def list_reports_for_issue(issue_id: str) -> list[SavedReport]:
    with connect() as db:
        rows = db.execute("SELECT * FROM reports WHERE issue_id=? ORDER BY created_at DESC", (issue_id,)).fetchall()
    return [_report_from_row(row) for row in rows]


def confirm_issue(issue_id: str, session_key: str) -> bool:
    with connect() as db:
        inserted = db.execute("INSERT OR IGNORE INTO confirmations VALUES (?, ?, ?)",
                              (issue_id, session_key, _now())).rowcount
        if inserted:
            db.execute("UPDATE issues SET confirmation_count=confirmation_count+1 WHERE id=?", (issue_id,))
    return bool(inserted)


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
        db.execute("""UPDATE issues SET after_photo=?, status=?, resolution_status=?,
            resolution_confidence=?, resolution_note=? WHERE id=?""",
            (after_photo, status, assessment.status, assessment.confidence, note, issue_id),
        )


def record_model_run(*, model_name: str, task: str, validation_status: str, result: dict) -> None:
    with connect() as db:
        db.execute("INSERT INTO model_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (str(uuid.uuid4()), model_name, task, "civicfix-v2", _now(),
                    validation_status, json.dumps(result, ensure_ascii=False)))
