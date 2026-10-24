from __future__ import annotations

import hashlib
import hmac
import html
import csv
import json
import os
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path

import folium
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageOps
from streamlit_folium import st_folium

from civicfix.ai import get_model, local_fallback
from civicfix.issue_service import report_quality, score_report, suggest_duplicate
from civicfix.models import CivicIssue, ComplaintDraft, ResolutionAssessment
from civicfix.priority import freshness_score, priority_label
from civicfix.routing import recommendations
from civicfix.storage import (
    confirmation_counts, confirm_issue, count_confirmations_for_session, get_government_resolutions,
    get_issue, get_issue_by_public_id, initialize, issue_timeline, list_issues,
    list_reports, list_reports_for_issue, merge_issues, record_model_run, save_government_verification,
    save_report, save_resolution, start_authority_work, submit_government_resolution, update_issue_status,
)


JURISDICTION, _ = recommendations("Other civic issue")
CATEGORIES = list(ComplaintDraft.model_fields["category"].annotation.__args__)
REPORT_STATUSES = ["Draft", "Ready to submit", "Submitted", "In progress", "Resolved"]
ISSUE_STATUSES = ["Open", "In progress", "Partially resolved", "Resolved", "Uncertain"]
PAGES = ["Home", "Scan issue", "My reports", "All reports", "Civic map", "Issues", "Government Resolutions", "Authority Portal", "Insights", "Profile"]
PAGE_ICONS = {
    "Home": "⌂", "Scan issue": "⊕", "My reports": "▤", "All reports": "▤", "Civic map": "⌖",
    "Issues": "◉", "Government Resolutions": "✓", "Authority Portal": "▣", "Insights": "▥", "Profile": "○",
}
DEFAULT_MAP_CENTER = (26.45, 80.33)  # Approximate Kanpur map viewport only; never stored as a report location.
location_picker_component = components.declare_component(
    "civicfix_location_picker",
    path=str(Path(__file__).resolve().parent / "components" / "location_picker"),
)


def _css(dark: bool) -> str:
    bg, surface, text, muted, border = (
        ("#101820", "#18232d", "#edf2f4", "#9aaab4", "#2b3a45") if dark
        else ("#f3f5f2", "#ffffff", "#15232b", "#66767d", "#dfe6e1")
    )
    return f"""
    <style>
    :root {{ --civic-bg:{bg}; --civic-surface:{surface}; --civic-text:{text}; --civic-muted:{muted}; --civic-border:{border}; --civic-green:#167a55; --civic-green-soft:#e1f1e8; --civic-amber:#c58018; --civic-red:#b7473b; }}
    html, body, [data-testid="stAppViewContainer"] {{ background:var(--civic-bg); color:var(--civic-text); }}
    [data-testid="stHeader"] {{ background:transparent; }}
    [data-testid="stSidebar"] {{ background:#14212a; border-right:1px solid #25343d; }}
    [data-testid="stSidebar"] * {{ color:#edf3f2; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] label {{ border-radius:9px; padding:7px 10px; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{ background:#22333b; }}
    .block-container {{ max-width:1440px; padding-top:1.65rem; padding-bottom:5rem; }}
    h1,h2,h3,h4,p,li {{ color:var(--civic-text); }}
    p,li,.stCaption {{ line-height:1.55; }}
    [data-testid="stMetric"] {{ background:var(--civic-surface); border:1px solid var(--civic-border); border-radius:14px; padding:14px 16px; box-shadow:0 2px 8px rgba(18,35,42,.035); }}
    [data-testid="stMetricLabel"] p {{ color:var(--civic-muted) !important; font-size:.82rem; }}
    [data-testid="stMetricValue"] {{ color:var(--civic-text) !important; font-weight:700; }}
    .stButton > button, .stLinkButton > a {{ min-height:44px; border-radius:10px; font-weight:650; border:1px solid var(--civic-border); }}
    .stButton > button[kind="primary"] {{ background:#167a55; border-color:#167a55; color:white; }}
    .stButton > button:hover {{ border-color:#167a55; color:#167a55; }}
    .stButton > button:focus-visible, input:focus-visible, textarea:focus-visible {{ outline:3px solid #65bd91 !important; outline-offset:2px; }}
    [data-testid="stForm"], [data-testid="stExpander"] {{ border-color:var(--civic-border) !important; border-radius:14px !important; }}
    [data-testid="stExpander"] {{ background:var(--civic-surface); }}
    [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea, [data-testid="stNumberInput"] input {{ background:var(--civic-surface); color:var(--civic-text); border-radius:9px; }}
    [data-testid="stFileUploader"] section {{ border:1px dashed #8ca398; border-radius:14px; background:var(--civic-surface); }}
    [data-testid="stProgress"] > div > div {{ background:#167a55; }}
    .brand {{ display:flex; align-items:center; gap:11px; padding:4px 2px 12px; }}
    .brand-mark {{ display:grid; place-items:center; width:38px; height:38px; color:white; background:#167a55; border-radius:12px; font-weight:800; font-size:19px; }}
    .brand-name {{ font-size:1.12rem; font-weight:750; letter-spacing:-.02em; color:#f4f8f6; }}
    .brand-sub {{ color:#9cb0ad; font-size:.70rem; letter-spacing:.08em; text-transform:uppercase; }}
    .eyebrow {{ color:#167a55; font-size:.72rem; letter-spacing:.13em; font-weight:750; text-transform:uppercase; margin:0 0 .4rem; }}
    .page-title {{ color:var(--civic-text); font-size:2rem; line-height:1.12; font-weight:760; letter-spacing:-.045em; margin:0 0 .4rem; }}
    .page-subtitle {{ color:var(--civic-muted); max-width:760px; margin:0 0 1.3rem; }}
    .hero {{ background:#e6f1e9; border:1px solid #cfe0d3; border-radius:20px; padding:clamp(24px,4vw,46px); min-height:205px; }}
    .hero-title {{ color:#142c24; font-size:clamp(1.8rem,4vw,3rem); font-weight:760; letter-spacing:-.05em; line-height:1.06; margin:0 0 12px; }}
    .hero-copy {{ color:#4b6557; font-size:1rem; max-width:560px; margin:0; }}
    .section-heading {{ display:flex; align-items:center; justify-content:space-between; margin:1.25rem 0 .8rem; }}
    .section-heading h3 {{ margin:0; font-size:1.13rem; letter-spacing:-.02em; }}
    .soft-card {{ background:var(--civic-surface); border:1px solid var(--civic-border); border-radius:15px; padding:16px; }}
    .issue-row {{ background:var(--civic-surface); border:1px solid var(--civic-border); border-radius:13px; padding:14px 16px; margin:.45rem 0; }}
    .pill {{ display:inline-block; border-radius:999px; padding:4px 9px; font-size:.72rem; font-weight:700; background:#e7ede9; color:#354b40; }}
    .pill-high {{ background:#fae9e5; color:#a53a30; }} .pill-medium {{ background:#fff1d8; color:#87520b; }} .pill-low {{ background:#e4f2e9; color:#176344; }}
    .scan-frame {{ border:1px solid var(--civic-border); border-radius:18px; padding:14px; background:var(--civic-surface); }}
    .scan-title {{ font-weight:700; margin:0 0 6px; }} .scan-hint {{ color:var(--civic-muted); font-size:.88rem; margin:0 0 10px; }}
    .demo-banner {{ border:1px solid #e2c88f; background:#fff6e4; color:#715211; border-radius:10px; padding:9px 12px; font-size:.85rem; font-weight:650; }}
    .fresh-dot {{ color:#16865b; font-size:1.05rem; }}
    @media (max-width: 740px) {{ .block-container {{ padding:1rem .8rem 5rem; }} .page-title {{ font-size:1.65rem; }} .hero {{ min-height:unset; padding:22px; }} [data-testid="stMetric"] {{ padding:11px; }} }}
    </style>
    """


def _html(content: str) -> None:
    st.markdown(content, unsafe_allow_html=True)


def _header(eyebrow: str, title: str, subtitle: str) -> None:
    _html(f'<div class="eyebrow">{html.escape(eyebrow)}</div><h1 class="page-title">{html.escape(title)}</h1><p class="page-subtitle">{html.escape(subtitle)}</p>')


def _goto(page: str) -> None:
    # Navigation is a keyed widget. Defer changing its value until the next
    # run, before st.radio is instantiated, to avoid Streamlit's widget-state error.
    st.session_state["pending_page"] = page
    st.rerun()


def _image_bytes(upload) -> bytes | None:
    if upload is None:
        return None
    image = Image.open(BytesIO(upload.getvalue()))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((1600, 1600))
    output = BytesIO()
    image.save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()


def _demo_issues() -> list[CivicIssue]:
    """Illustrative, non-persistent demo records; no coordinates or real reports."""
    return [
        CivicIssue(id="DEMO-CF-1001", public_id="CF-1001", category="Pothole / damaged road", summary="Large pothole near the main gate", location_text="Sample location A · no GPS", status="Resolved", authority_status="Resolved", first_seen="2026-10-20T10:00:00+00:00", last_seen="2026-10-24T08:00:00+00:00", priority_score=89, report_count=14, confirmation_count=8, severity=86, public_impact=78, safety_risk=82, evidence_confidence=.91, evidence_quality=91),
        CivicIssue(id="DEMO-CF-1002", public_id="CF-1002", category="Broken streetlight", summary="Streetlight out near a crossing", location_text="Sample location B · no GPS", status="In progress", authority_status="In progress", first_seen="2026-10-22T09:00:00+00:00", last_seen="2026-10-23T15:00:00+00:00", priority_score=62, report_count=5, confirmation_count=2, severity=59, public_impact=65, safety_risk=66, evidence_confidence=.7, evidence_quality=72),
        CivicIssue(id="DEMO-CF-1003", public_id="CF-1003", category="Garbage / sanitation", summary="Overflowing public waste bin", location_text="Sample location C · no GPS", status="Resolved", authority_status="Resolved", first_seen="2026-10-21T18:00:00+00:00", last_seen="2026-10-24T08:00:00+00:00", priority_score=70, report_count=3, confirmation_count=1, severity=55, public_impact=68, safety_risk=76, evidence_confidence=.73, evidence_quality=69),
        CivicIssue(id="DEMO-CF-1004", public_id="CF-1004", category="Open drain", summary="Uncovered drain beside a walkway", location_text="Sample location D · no GPS", status="Open", first_seen="2026-10-20T12:00:00+00:00", last_seen="2026-10-24T07:00:00+00:00", priority_score=82, report_count=7, confirmation_count=4, severity=82, public_impact=76, safety_risk=90, evidence_confidence=.84, evidence_quality=86),
        CivicIssue(id="DEMO-CF-1005", public_id="CF-1005", category="Damaged footpath", summary="Broken paving blocks on a public footpath", location_text="Sample location E · no GPS", status="Partially resolved", authority_status="Partially resolved", first_seen="2026-10-18T11:00:00+00:00", last_seen="2026-10-22T11:00:00+00:00", priority_score=38, report_count=4, confirmation_count=3, severity=48, public_impact=57, safety_risk=38, evidence_confidence=.71, evidence_quality=77),
    ]


def _issues(demo_mode: bool) -> list[CivicIssue]:
    actual = list_issues()
    combined = actual + (_demo_issues() if demo_mode else [])
    return sorted(combined, key=lambda item: (item.priority_score, item.last_seen), reverse=True)


def _status_pill(issue: CivicIssue) -> str:
    level = priority_label(issue.priority_score).lower()
    label = f"{level} priority" if issue.status != "Resolved" else "resolved"
    color_level = "low" if issue.status == "Resolved" else level
    return f'<span class="pill pill-{color_level}">{html.escape(label)}</span>'


def _freshness_label(last_seen: str) -> tuple[str, str]:
    try:
        when = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        hours = max(0, (datetime.now(timezone.utc) - when).total_seconds() / 3600)
    except (ValueError, TypeError):
        hours = 24 * 31
    if hours < 24:
        return "Fresh", f"Updated {max(1, int(hours))}h ago"
    days = int(hours / 24)
    if days < 14:
        return "Aging", f"Updated {days}d ago"
    return "Stale", f"Updated {days}d ago"


def _priority_breakdown(issue: CivicIssue) -> None:
    freshness = freshness_score(issue.last_seen)
    factors = [
        ("Severity", issue.severity, "Visible extent and obstruction"),
        ("Public impact", issue.public_impact, "Potential effect on public space users"),
        ("Safety risk", issue.safety_risk, "Visible risk estimate, not emergency advice"),
        ("Evidence confidence", round(issue.evidence_confidence * 100), "Model confidence in the supplied evidence"),
        ("Freshness", round(freshness), "Recent evidence receives more weight"),
    ]
    for label, score, detail in factors:
        cols = st.columns([1.1, 3, .45])
        cols[0].markdown(f"**{label}**")
        cols[1].progress(max(0, min(100, int(score))))
        cols[2].markdown(f"**{score}**")
        st.caption(detail)


def _route_links(category: str) -> None:
    _, routes = recommendations(category)
    for route in routes:
        st.link_button(f"Open {route.authority}", route.url, use_container_width=True)
        st.caption(f"{route.channel}. {route.note}")


def _get_reviewer_access() -> bool:
    configured = os.getenv("CIVICFIX_REVIEW_KEY", "")
    if not configured:
        st.sidebar.caption("Reviewer controls are disabled. Configure CIVICFIX_REVIEW_KEY in .env to enable them.")
        return False
    candidate = st.sidebar.text_input("Reviewer access", type="password", key="reviewer_access_input", placeholder="Reviewer key")
    return bool(candidate) and hmac.compare_digest(candidate, configured)


def _render_home(issues: list[CivicIssue], demo_mode: bool, citizen_key: str) -> None:
    _header("CIVICFIX / HOME", "Spot something broken?", "Scan it. CivicFix assesses the evidence, helps prioritize it, and prepares a report you can review.")
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        _html('<div class="hero"><p class="eyebrow">SEE · SCAN · TRACK</p><h2 class="hero-title">Make a local issue<br>visible and actionable.</h2><p class="hero-copy">Capture the problem, confirm its location and get a clear, evidence-based assessment.</p></div>')
        a, b = st.columns(2)
        if a.button("⊕  Scan a civic issue", type="primary", use_container_width=True):
            _goto("Scan issue")
        if b.button("⌖  View civic map", use_container_width=True):
            _goto("Civic map")
    with right:
        mine = list_reports(owner_session_key=citizen_key)
        confirmed = count_confirmations_for_session(citizen_key)
        issue_by_id = {issue.id: issue for issue in list_issues()}
        resolved = len({r.issue_id for r in mine if r.issue_id and r.issue_id in issue_by_id and issue_by_id[r.issue_id].status == "Resolved"})
        st.markdown('<div class="section-heading"><h3>Your civic activity</h3></div>', unsafe_allow_html=True)
        stats = st.columns(3)
        stats[0].metric("Reports", len(mine))
        stats[1].metric("Confirmed", confirmed)
        stats[2].metric("Resolved", resolved)
        st.caption("Personal to this browser session; no account is required in this prototype.")

    _html('<div class="section-heading"><h3>Nearby and recent issues</h3></div>')
    display_issues = issues[:3]
    if demo_mode:
        _html('<div class="demo-banner">Prototype Demo Data · sample records are illustrative, not real civic reports.</div>')
        display_issues = [i for i in issues if i.id.startswith("DEMO-")][:3]
    if not display_issues:
        st.info("No reports yet. Be the first to scan a civic issue.")
    for issue in display_issues:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.2, .8])
            c1.markdown(f"**{issue.public_id} · {issue.summary}**\n\n{issue.category} · {issue.location_text or 'Location not confirmed'}")
            c2.markdown(_status_pill(issue), unsafe_allow_html=True)
            c2.caption(f"{issue.report_count} observation(s)")
            c3.metric("Priority", f"{issue.priority_score:.0f}")
            if c1.button("View issue", key=f"home-issue-{issue.id}"):
                st.session_state["selected_issue_id"] = issue.id
                _goto("Issues")
    with st.expander("Map preview"):
        _render_map(issues, demo_mode, height=270)
    recent = get_government_resolutions()[:3]
    if recent:
        st.markdown("#### Recently resolved by authorities")
        issue_by_id = {issue.id: issue for issue in list_issues()}
        for resolution in recent:
            issue = issue_by_id.get(resolution.issue_id)
            if not issue:
                continue
            with st.container(border=True):
                st.markdown(f"**{issue.public_id} · {issue.summary}**")
                st.caption(f"{issue.location_text or 'Location not confirmed'} · {resolution.status} · {resolution.resolution_date} · {resolution.authority}")
                if resolution.evidence_photos:
                    st.image(resolution.evidence_photos[0], caption="Authority evidence", width=180)
                if st.button("View resolution", key=f"home-resolution-{resolution.id}"):
                    st.session_state["resolution-search"] = issue.public_id
                    _goto("Government Resolutions")
    elif demo_mode:
        st.markdown("#### Recently resolved · Prototype Demo Data")
        for row in _demo_resolution_records():
            issue = next((item for item in issues if item.public_id == row["issue_id"]), None)
            if issue and row["status"] in {"Resolved", "Partially resolved"}:
                st.caption(f"✓ {issue.summary} · {row['status']} · {row['date']} · {row['department']}")


def _render_scan(citizen_key: str, demo_mode: bool) -> None:
    _header("CIVICFIX / SCAN", "Scan a civic issue", "Use your camera or upload a photo. CivicFix combines the image and your description into one validated assessment.")
    if demo_mode:
        st.info("Prototype sample cards are read-only. Scans you submit here are real reports saved to your session history.")
    capture, details = st.columns([1.05, 1], gap="large")
    with capture:
        _html('<div class="scan-frame"><p class="scan-title">Evidence capture</p><p class="scan-hint">Frame the public issue clearly. Avoid faces, number plates and private documents.</p></div>')
        camera_photo = st.camera_input("Take a photo", key="camera-capture", label_visibility="collapsed")
        gallery_photo = st.file_uploader("Upload from gallery", type=["jpg", "jpeg", "png", "webp"], key="gallery-upload", help="JPG, PNG or WEBP · image is compressed before analysis")
        photo = gallery_photo or camera_photo
        preview_bytes = None
        invalid_photo = False
        if photo:
            try:
                preview_bytes = _image_bytes(photo)
                st.image(preview_bytes, caption="Selected evidence photo", use_container_width=True)
            except Exception:
                invalid_photo = True
                st.error("Please upload a valid JPG, PNG or WEBP image.")
    with details:
        st.markdown("#### What did you notice?")
        description = st.text_area("Describe the issue", key="scan-description", max_chars=4000, height=110, placeholder="For example: A deep pothole has formed beside the crossing and forces people into traffic.")
        location_choice = location_picker_component(maps_key=_google_maps_key(), key="scan-location-picker", default=None)
        if isinstance(location_choice, dict) and location_choice.get("token") != st.session_state.get("processed-location-token"):
            st.session_state["processed-location-token"] = location_choice.get("token")
            st.session_state["scan_lat"] = float(location_choice["lat"])
            st.session_state["scan_lon"] = float(location_choice["lng"])
            st.session_state["scan-location"] = str(location_choice.get("location", ""))
            st.rerun()
        location = st.text_input("Landmark or street", key="scan-location", placeholder="Near the library entrance")
        confirmed = st.session_state.get("scan_lat") is not None and st.session_state.get("scan_lon") is not None
        if confirmed:
            st.success(f"Location pinned · {st.session_state['scan_lat']:.5f}, {st.session_state['scan_lon']:.5f}")
        else:
            st.warning("Location not confirmed · add a landmark or choose a point on the map.")
        choose_map = st.checkbox("Choose location on map", key="show-location-map")
        if choose_map:
            center = (st.session_state.get("scan_lat") or DEFAULT_MAP_CENTER[0], st.session_state.get("scan_lon") or DEFAULT_MAP_CENTER[1])
            picker = folium.Map(location=center, zoom_start=14, control_scale=True)
            if confirmed:
                folium.Marker(center, tooltip="Selected location").add_to(picker)
            pin = st_folium(picker, height=250, key="location-picker", returned_objects=["last_clicked"])
            click = pin.get("last_clicked") if pin else None
            if click:
                st.session_state["scan_lat"] = float(click["lat"])
                st.session_state["scan_lon"] = float(click["lng"])
                st.rerun()
            st.caption("Click the map to set coordinates. The map viewport is approximate; no coordinates are saved until you select a point.")
        with st.expander("Enter coordinates manually (optional)"):
            la, lo = st.columns(2)
            lat = la.number_input("Latitude", min_value=-90.0, max_value=90.0, value=st.session_state.get("scan_lat"), format="%.6f", key="manual-latitude")
            lon = lo.number_input("Longitude", min_value=-180.0, max_value=180.0, value=st.session_state.get("scan_lon"), format="%.6f", key="manual-longitude")
            if st.button("Use these coordinates", key="use-coordinates"):
                if lat is None or lon is None:
                    st.error("Enter both latitude and longitude.")
                else:
                    st.session_state["scan_lat"] = lat
                    st.session_state["scan_lon"] = lon
                    st.rerun()
        provider = os.getenv("AI_PROVIDER", "gemini").lower()
        provider_label = "Google Gemini" if provider == "gemini" else provider
        consent_text = f"I consent to send this description and selected photo to {provider_label} for analysis." if photo else f"I consent to send this description to {provider_label} for analysis."
        consent = st.checkbox(consent_text, key="scan-consent") if provider != "none" else True
        analyze = st.button("Analyze civic evidence", type="primary", use_container_width=True)

    current_signature = hashlib.sha256(
        (description.strip() + location.strip()).encode("utf-8") + (preview_bytes or b"")
    ).hexdigest()
    if st.session_state.get("analysis_signature") and st.session_state["analysis_signature"] != current_signature:
        st.session_state.pop("analysis", None)
        st.session_state.pop("analysis_signature", None)
        st.session_state.pop("scan_report_id", None)
        st.session_state.pop("scan_issue_id", None)

    if analyze:
        if not description.strip():
            st.error("Add a short description so the model can validate the issue.")
            return
        if provider != "none" and not consent:
            st.error("Consent is required before sending report content to the configured model.")
            return
        if invalid_photo:
            st.error("Please upload a valid image file.")
            return
        image_bytes = preview_bytes
        if image_bytes and len(image_bytes) > 8 * 1024 * 1024:
            st.error("The compressed image exceeds 8 MB. Choose a smaller photo.")
            return
        if not image_bytes:
            st.info("No photo selected. Text-only analysis may have lower evidence confidence.")
        try:
            with st.spinner("Analyzing image and generating a civic assessment…"):
                model = get_model(provider)
                if model is None:
                    draft = local_fallback(description, location)
                    model_name = "Local template (AI disabled)"
                else:
                    draft = model.draft(description.strip(), location.strip(), image_bytes)
                    model_name = f"{model.name} ({getattr(model, 'model', 'configured model')})"
                signature = hashlib.sha256(
                    (description.strip() + location.strip()).encode("utf-8") + (image_bytes or b"")
                ).hexdigest()
                st.session_state["analysis"] = draft.model_dump()
                st.session_state["analysis_signature"] = signature
                st.session_state["editable-complaint"] = draft.draft_complaint
                st.session_state["scan_source_description"] = description.strip()
                st.session_state["scan_location"] = location.strip()
                st.session_state["scan_photo"] = image_bytes
                st.session_state["scan_mode"] = model_name
                st.session_state["scan_report_id"] = None
                st.session_state["scan_issue_id"] = None
                record_model_run(model_name=model_name, task="issue_analysis", validation_status=draft.validation_status, result=draft.model_dump())
        except Exception as error:
            st.error(f"AI analysis is temporarily unavailable: {error}")
            st.info("No report has been saved yet. Your description and photo remain in this browser session. Retry analysis in a few minutes; after it succeeds, choose Save & open report channel to add it to All reports.")

    _render_scan_result(citizen_key)


def _render_scan_result(citizen_key: str) -> None:
    raw = st.session_state.get("analysis")
    if not raw:
        return
    draft = ComplaintDraft.model_validate(raw)
    st.divider()
    _html('<div class="eyebrow">CIVICFIX ANALYSIS</div>')
    if draft.validation_status != "valid" or not draft.is_civic_issue or draft.confidence < .55:
        st.warning("We need a little more information." if draft.validation_status == "ambiguous" else "This evidence does not appear to show a public civic issue.")
        st.write(draft.reasoning_summary or "The evidence was not clear enough for a reliable civic assessment.")
        if draft.follow_up_questions:
            st.markdown("**To improve the report:** " + " · ".join(draft.follow_up_questions))
        if draft.missing_information:
            st.markdown("**Missing:** " + " · ".join(draft.missing_information))
        st.caption("No issue has been saved. Add context or upload a clearer image, then analyze again.")
        return

    score = score_report(draft)
    quality = report_quality(draft, has_photo=bool(st.session_state.get("scan_photo")),
                             has_location=bool(st.session_state.get("scan_location") or st.session_state.get("scan_lat")))
    title, metric = st.columns([2, 1])
    with title:
        st.success("Civic issue detected")
        st.markdown(f"### {draft.category.upper()}")
        st.write(draft.summary)
        st.caption(f"Evidence analysis · {st.session_state.get('scan_mode', 'Configured model')}")
    metric.metric("Priority", f"{score:.0f} / 100", priority_label(score))
    flow = st.columns(5)
    for column, step, value in zip(flow, ["Evidence", "Validation", "Assessment", "Priority", "Complaint"], ["Received", "Passed", "Structured", "Scored", "Generated"]):
        column.markdown(f"**{step}**")
        column.caption(value)

    breakdown, report = st.columns([.95, 1.2], gap="large")
    with breakdown:
        with st.container(border=True):
            st.markdown("#### Why this priority?")
            _priority_breakdown(CivicIssue(
                id="PREVIEW", category=draft.category, summary=draft.summary,
                first_seen=datetime.now(timezone.utc).isoformat(), last_seen=datetime.now(timezone.utc).isoformat(),
                priority_score=score, severity=draft.severity, public_impact=draft.public_impact,
                safety_risk=draft.safety_risk, evidence_confidence=draft.confidence,
                evidence_quality=draft.evidence_quality,
            ))
            st.caption("Prototype score, not an official policy or safety assessment.")
        with st.container(border=True):
            st.markdown("#### Report quality")
            st.metric("Completeness", f"{quality}/100")
            checks = [
                ("Clear image", bool(st.session_state.get("scan_photo"))),
                ("Issue visible", draft.evidence_quality >= 60),
                ("Location available", bool(st.session_state.get("scan_location") or st.session_state.get("scan_lat"))),
                ("Description consistent", draft.confidence >= .7),
            ]
            for label, passed in checks:
                st.write(("✓ " if passed else "○ ") + label)
            if draft.missing_information:
                st.warning("Missing: " + "; ".join(draft.missing_information))
    with report:
        with st.container(border=True):
            st.markdown("#### Civic issue report")
            st.caption("AI generated · review and edit before saving")
            issue_text = st.text_area("Complaint", draft.draft_complaint, key="editable-complaint", height=180, max_chars=4000)
            st.write(f"**Location:** {st.session_state.get('scan_location') or 'Location not confirmed'}")
            st.write(f"**Recommended department:** {draft.suggested_department} · unverified")
            st.write(f"**Severity / impact / safety:** {draft.severity} / {draft.public_impact} / {draft.safety_risk}")

    candidate_id, candidate_score = suggest_duplicate(
        draft, st.session_state.get("scan_source_description", ""),
        st.session_state.get("scan_location", ""), st.session_state.get("scan_lat"),
        st.session_state.get("scan_lon"),
    )
    candidate = get_issue(candidate_id) if candidate_id else None
    group_as_same = False
    if candidate:
        with st.container(border=True):
            st.warning("Possible existing issue found")
            st.markdown(f"**{candidate.summary}** · {candidate.report_count} observation(s) · Priority {candidate.priority_score:.0f}")
            st.caption(f"Candidate match score {candidate_score:.2f}. This is a heuristic suggestion; review before grouping.")
            choice = st.radio("How should this report be handled?", ["This is different / new issue", "Confirm same issue"], horizontal=True, key=f"duplicate-choice-{candidate.id}")
            group_as_same = choice == "Confirm same issue"
    else:
        st.info("New civic issue · no likely duplicate passed the current category/location/text checks.")

    save_cols = st.columns([1, 1, 2])
    save_draft = save_cols[0].button("Save draft", use_container_width=True)
    prepare_submission = save_cols[1].button("Save & open report channel", type="primary", use_container_width=True)
    if save_draft or prepare_submission:
        # Keep the generated scoring and classification separate from edited complaint prose.
        revised = draft.model_copy(update={"draft_complaint": issue_text.strip() or draft.draft_complaint})
        saved = save_report(
            report_id=st.session_state.get("scan_report_id"),
            status="Draft" if save_draft else "Ready to submit", external_reference="",
            citizen_description=st.session_state.get("scan_source_description", ""),
            location_text=st.session_state.get("scan_location", ""),
            latitude=st.session_state.get("scan_lat"), longitude=st.session_state.get("scan_lon"),
            photo_jpeg=st.session_state.get("scan_photo"), draft=revised,
            issue_id=candidate.id if candidate and group_as_same else None,
            priority_score=score, owner_session_key=citizen_key,
        )
        st.session_state["scan_report_id"] = saved.id
        st.session_state["scan_issue_id"] = saved.issue_id
        saved_issue = get_issue(saved.issue_id) if saved.issue_id else None
        st.success(f"Saved to your reports · Issue {saved_issue.public_id if saved_issue else 'created'}")
        if prepare_submission:
            st.caption("CivicFix has prepared the report. The external portal still requires you to submit it.")
            _route_links(draft.category)
    if st.session_state.get("scan_issue_id"):
        if st.button("View issue timeline", key="open-created-issue"):
            st.session_state["selected_issue_id"] = st.session_state["scan_issue_id"]
            _goto("Issues")


def _render_my_reports(citizen_key: str) -> None:
    _header("CIVICFIX / REPORTS", "Your reports", "Drafts and reports saved in this browser session.")
    st.caption("This prototype uses a browser-session identifier, not an account. Reports remain stored on the server and may not appear here after you change browser or clear session data.")
    reports = list_reports(owner_session_key=citizen_key)
    if not reports:
        st.info("Nothing saved yet. Scan an issue to start your report history.")
        if st.button("Scan a civic issue", type="primary"):
            _goto("Scan issue")
        return
    filters = st.multiselect("Filter by status", REPORT_STATUSES, default=REPORT_STATUSES, key="my-report-filter")
    for report in reports:
        if report.status not in filters:
            continue
        with st.container(border=True):
            c1, c2, c3 = st.columns([2.2, 1, 1.1])
            c1.markdown(f"**{report.draft.summary}**\n\n{report.draft.category} · {report.location_text or 'Location not confirmed'}")
            c2.write(report.status)
            c3.write(report.created_at[:10])
            with st.expander("View report and update your tracking status"):
                if report.photo_jpeg:
                    st.image(report.photo_jpeg, caption="Your saved evidence", width=300)
                st.text_area("Complaint", report.draft.draft_complaint, key=f"report-text-{report.id}", disabled=True)
                col1, col2 = st.columns([1, 1])
                with col1:
                    with st.form(f"my-report-status-{report.id}"):
                        status = st.selectbox("Status you observed", REPORT_STATUSES,
                                              index=REPORT_STATUSES.index(report.status), key=f"my-status-{report.id}")
                        reference = st.text_input("External portal reference", report.external_reference, key=f"my-ref-{report.id}")
                        update = st.form_submit_button("Save status")
                    if update:
                        save_report(
                            report_id=report.id, status=status, external_reference=reference,
                            citizen_description=report.citizen_description, location_text=report.location_text,
                            latitude=report.latitude, longitude=report.longitude, photo_jpeg=None,
                            draft=report.draft, issue_id=report.issue_id,
                            owner_session_key=citizen_key,
                        )
                        st.rerun()
                with col2:
                    if report.issue_id and st.button("Open issue", key=f"open-my-issue-{report.id}"):
                        st.session_state["selected_issue_id"] = report.issue_id
                        _goto("Issues")


def _render_all_reports(reviewer: bool) -> None:
    _header("CIVICFIX / REPORTS", "All citizen reports", "Review every report saved by users, across all civic issue groups.")
    if not reviewer:
        st.info("All reports contain private descriptions and evidence. Enter the reviewer key in the sidebar to view them.")
        return

    reports = list_reports()
    issue_public_ids = {issue.id: issue.public_id for issue in list_issues()}
    if not reports:
        st.info("No reports have been saved yet. A report appears here after its author selects Save & open report channel.")
        return

    controls = st.columns([1.2, 1, 1.5])
    query = controls[0].text_input("Search reports", placeholder="Summary, location or report ID", key="all-reports-search")
    statuses = controls[1].multiselect("Status", REPORT_STATUSES, default=REPORT_STATUSES, key="all-reports-status")
    categories = sorted({report.draft.category for report in reports})
    selected_categories = controls[2].multiselect("Category", categories, default=categories, key="all-reports-category")

    needle = query.strip().casefold()
    filtered = [report for report in reports
                if report.status in statuses
                and report.draft.category in selected_categories
                and (not needle or needle in " ".join((report.id, report.draft.summary, report.draft.category,
                                                       report.location_text, report.citizen_description)).casefold())]
    st.caption(f"Showing {len(filtered)} of {len(reports)} saved reports · newest activity first")
    if not filtered:
        st.info("No reports match these filters.")
        return

    csv_buffer = StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["report_id", "created_at", "status", "category", "summary", "location", "civicfix_issue_id", "description"])
    for report in filtered:
        writer.writerow([report.id, report.created_at, report.status, report.draft.category,
                         report.draft.summary, report.location_text, issue_public_ids.get(report.issue_id, ""),
                         report.citizen_description])
    st.download_button("Download filtered reports (CSV)", csv_buffer.getvalue(),
                       file_name="civicfix-reports.csv", mime="text/csv", key="download-all-reports")

    st.dataframe([
        {"Reported": report.created_at[:16].replace("T", " "), "Status": report.status,
         "Category": report.draft.category, "Issue": report.draft.summary,
         "Location": report.location_text or "Not provided", "CivicFix ID": issue_public_ids.get(report.issue_id, ""),
         "Report ID": report.id[:8]}
        for report in filtered
    ], hide_index=True, use_container_width=True)

    st.markdown("#### Report details")
    for report in filtered:
        label = f"{report.created_at[:10]} · {report.draft.category} · {report.draft.summary} · {report.status}"
        with st.expander(label):
            st.caption(f"Report {report.id} · Issue {issue_public_ids.get(report.issue_id, 'not grouped')} · Updated {report.updated_at[:16].replace('T', ' ')}")
            st.write(f"**Location:** {report.location_text or 'Not provided'}")
            st.write(f"**Description:** {report.citizen_description}")
            st.markdown("**Prepared complaint**")
            st.write(report.draft.draft_complaint)
            if report.external_reference:
                st.write(f"**External portal reference:** {report.external_reference}")
            if report.photo_jpeg:
                st.image(report.photo_jpeg, caption="Submitted evidence", width=420)


def _demo_resolution_records() -> list[dict[str, str]]:
    return [
        {"issue_id": "CF-1001", "status": "Resolved", "department": "Municipal Road Maintenance", "authority": "Demo Municipal Authority", "date": "2026-10-24", "notes": "Road surface repaired and damaged section restored."},
        {"issue_id": "CF-1002", "status": "In progress", "department": "Public Lighting", "authority": "Demo Municipal Authority", "date": "2026-10-24", "notes": "Replacement lamp and electrical inspection scheduled."},
        {"issue_id": "CF-1003", "status": "Resolved", "department": "Sanitation Services", "authority": "Demo Municipal Authority", "date": "2026-10-23", "notes": "Waste removed and collection frequency reviewed."},
        {"issue_id": "CF-1005", "status": "Partially resolved", "department": "Footpath Maintenance", "authority": "Demo Municipal Authority", "date": "2026-10-22", "notes": "One section repaired; remaining damaged paving is awaiting work."},
    ]


def _render_government_resolutions(issues: list[CivicIssue], reviewer: bool, citizen_key: str, demo_mode: bool) -> None:
    _header("CIVICFIX / GOVERNMENT RESOLUTIONS", "Government resolutions", "Track what participating authorities say they did, and what the evidence supports.")
    st.caption("Prototype authority submissions are not connected to or verified by a real government department.")
    resolutions = get_government_resolutions()
    issue_by_id = {issue.id: issue for issue in list_issues()}
    query = st.text_input("Search CivicFix Issue ID", placeholder="Search Issue ID e.g. CF-1001", key="resolution-search")
    status_options = ["In progress", "Resolved", "Partially resolved", "Still open", "Uncertain"]
    selected_statuses = st.multiselect("Authority status", status_options, default=status_options, key="resolution-status-filter")
    needle = query.strip().upper()
    actual = [(resolution, issue_by_id.get(resolution.issue_id), False) for resolution in resolutions]
    actual = [(resolution, issue, is_demo) for resolution, issue, is_demo in actual
              if issue and resolution.status in selected_statuses
              and (not needle or needle in issue.public_id.upper() or needle in issue.summary.upper())]
    demo_rows: list[tuple[dict[str, str], CivicIssue, bool]] = []
    if demo_mode:
        demo_by_public_id = {issue.public_id: issue for issue in issues if issue.id.startswith("DEMO-")}
        demo_rows = [(row, demo_by_public_id[row["issue_id"]], True) for row in _demo_resolution_records()
                     if row["issue_id"] in demo_by_public_id and row["status"] in selected_statuses
                     and (not needle or needle in row["issue_id"] or needle in demo_by_public_id[row["issue_id"]].summary.upper())]
        _html('<div class="demo-banner">Prototype Demo Data · sample authority records are fictional and are not persistent.</div>')
    displayed_issues = [issue for _, issue, _ in actual] + [issue for _, issue, _ in demo_rows]
    if displayed_issues:
        st.markdown("#### Resolved activity map")
        _render_google_map(displayed_issues, height=360)
    if not actual and not demo_rows:
        st.info("No authority response matches this search. Reports without an authority response appear under Issues.")
        return

    for resolution, issue, is_demo in actual:
        _resolution_card(resolution, issue, reviewer, citizen_key, is_demo)
    for row, issue, is_demo in demo_rows:
        with st.container(border=True):
            st.caption(f"PROTOTYPE DEMO DATA · CIVICFIX ISSUE {issue.public_id}")
            st.markdown(f"### {issue.summary}")
            st.write(f"**Location:** {issue.location_text}")
            st.write(f"**Government status:** {row['status']} · **Resolution date:** {row['date']}")
            st.write(f"**Department:** {row['department']} · **Authority:** {row['authority']}")
            st.write(row["notes"])
            st.info("AI verification: no evidence supplied in this illustrative demo record; not verified.")


def _resolution_card(resolution, issue: CivicIssue, reviewer: bool, citizen_key: str, is_demo: bool = False) -> None:
    with st.container(border=True):
        title_cols = st.columns([2.8, 1])
        title_cols[0].caption(f"CIVICFIX ISSUE {issue.public_id}")
        title_cols[0].markdown(f"### {issue.summary}")
        title_cols[1].metric("Government status", resolution.status)
        st.write(f"📍 {issue.location_text or 'Location not confirmed'}")
        st.write(f"**Department:** {resolution.department} · **Authority:** {resolution.authority}")
        st.write(f"**Resolution date:** {resolution.resolution_date} · **Work order:** {resolution.work_order_id or 'Not provided'}")
        st.write(resolution.resolution_notes)
        if resolution.verification:
            assessment = resolution.verification
            st.success(f"AI evidence assessment: {assessment.status.replace('_', ' ').title()} · {assessment.confidence:.0%} confidence") if assessment.status == "resolved" else st.warning(f"AI evidence assessment: {assessment.status.replace('_', ' ').title()} · {assessment.confidence:.0%} confidence")
            st.caption(f"Same-location likelihood {assessment.same_location_likelihood:.0%} · Issue persistence {assessment.issue_persistence:.0%} · Evidence quality {assessment.evidence_quality:.0%}")
        else:
            st.info("Authority reported · AI evidence verification pending.")
        if resolution.evidence_photos:
            images = st.columns(min(3, len(resolution.evidence_photos)))
            for index, (column, photo) in enumerate(zip(images, resolution.evidence_photos), start=1):
                column.image(photo, caption=f"Authority after photo {index}", use_container_width=True)
        else:
            st.caption("No resolution photographs were attached.")
        own_reports = list_reports_for_issue(issue.id)
        can_view_original = reviewer or any(report.owner_session_key == citizen_key for report in own_reports)
        if can_view_original and issue.before_photo:
            with st.expander("View original citizen evidence"):
                st.image(issue.before_photo, caption="Original report photo", width=520)
                if own_reports:
                    st.write(own_reports[0].citizen_description)
        elif issue.before_photo:
            st.caption("Original citizen evidence is limited to its author and authorized reviewers.")
        with st.expander("Issue timeline"):
            for event in issue_timeline(issue.id):
                st.write(f"{event['at'][:10]} · {event['event']}")
        with st.expander("Civic map and nearby Street View"):
            _render_issue_map_and_street_view(issue)


def _render_authority_portal(issues: list[CivicIssue], demo_mode: bool) -> None:
    _header("CIVICFIX / AUTHORITY PORTAL", "Authority portal", "Find an issue by its CivicFix ID and publish a transparent status update.")
    st.warning("Hackathon prototype · this portal is not connected to a real authority. Submissions are labeled as prototype authority records.")
    configured = os.getenv("CIVICFIX_AUTHORITY_KEY", "")
    if not configured:
        st.info("Authority submission is disabled. Configure CIVICFIX_AUTHORITY_KEY in the server .env to enable this prototype portal.")
        return
    candidate = st.text_input("Authority portal access", type="password", key="authority-access-input", placeholder="Authority access key")
    if not candidate or not hmac.compare_digest(candidate, configured):
        st.caption("Enter the authority access key to continue.")
        return

    actual_issues = list_issues()
    authority_resolutions = get_government_resolutions()
    kpis = st.columns(5)
    kpis[0].metric("Open assigned", sum(issue.status == "Open" and issue.authority_status is None for issue in actual_issues))
    kpis[1].metric("High priority", sum(issue.priority_score >= 75 and issue.status != "Resolved" for issue in actual_issues))
    kpis[2].metric("In progress", sum(issue.status == "In progress" for issue in actual_issues))
    kpis[3].metric("Authority resolved", sum(issue.authority_status == "Resolved" for issue in actual_issues))
    kpis[4].metric("Awaiting AI review", sum(resolution.verification is None for resolution in authority_resolutions))
    high_priority = [issue for issue in actual_issues if issue.priority_score >= 75 and issue.status != "Resolved"][:5]
    if high_priority:
        st.markdown("#### High-priority issues")
        for issue in high_priority:
            st.caption(f"{issue.public_id} · {issue.priority_score:.0f}/100 · {issue.category} · {issue.summary}")

    with st.form("authority-issue-search"):
        query = st.text_input("Search CivicFix Issue ID", placeholder="CF-1001", key="authority-issue-query")
        find_issue = st.form_submit_button("Find issue")
    if find_issue:
        st.session_state["authority_selected_issue"] = query.strip().upper()
    public_id = st.session_state.get("authority_selected_issue", "")
    if not public_id:
        st.info("Search by the CivicFix Issue ID shown on the Issues page or in a citizen report.")
        return
    issue = get_issue_by_public_id(public_id)
    if issue is None and demo_mode:
        issue = next((item for item in issues if item.public_id.upper() == public_id), None)
        if issue and issue.id.startswith("DEMO-"):
            _html('<div class="demo-banner">Prototype Demo Data · sample issue is read-only and not stored in the database.</div>')
            st.write(f"**{issue.public_id} · {issue.summary}** · {issue.category}")
            st.write(f"**Location:** {issue.location_text} · **Priority:** {issue.priority_score:.0f}/100 · **Status:** {issue.status}")
            st.info("Use a saved citizen issue to exercise the authority workflow. Demo data is read-only.")
            return
    if issue is None:
        st.error("No saved issue has that CivicFix ID. Check the ID and try again.")
        return

    st.markdown(f"### {issue.public_id} · {issue.summary}")
    st.write(f"**Category:** {issue.category} · **Priority:** {issue.priority_score:.0f}/100 · **Current status:** {issue.status}")
    st.write(f"**Location:** {issue.location_text or 'Not confirmed'}")
    reports = list_reports_for_issue(issue.id)
    if issue.before_photo:
        st.image(issue.before_photo, caption="Original citizen evidence", width=480)
    if reports:
        st.write(f"**Citizen description:** {reports[0].citizen_description}")
        st.write(f"**AI assessment:** severity {issue.severity} · safety risk {issue.safety_risk} · public impact {issue.public_impact}")
    if issue.latitude is not None and issue.longitude is not None:
        _render_issue_map_and_street_view(issue)
    if issue.status != "In progress" and st.button("Start resolution", type="primary", key=f"authority-start-{issue.id}"):
        start_authority_work(issue.id)
        st.success("Work marked in progress.")
        st.rerun()

    st.markdown("#### Publish authority response")
    with st.form(f"authority-resolution-form-{issue.id}"):
        department = st.text_input("Department", placeholder="Road Maintenance Department")
        authority = st.text_input("Authority", placeholder="Demo Municipal Authority")
        resolution_status = st.selectbox("Resolution status", ["Resolved", "Partially resolved", "Still open", "Uncertain"])
        resolution_date = st.date_input("Resolution date", value=date.today())
        notes = st.text_area("Resolution notes", placeholder="Describe the work completed and any remaining issue.")
        work_order = st.text_input("Work order / reference ID (optional)")
        uploads = st.file_uploader("Resolution photographs", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, help="Upload several clear after photos of the repaired area.")
        if uploads:
            st.caption(f"{len(uploads)} resolution photograph(s) selected.")
        provider = os.getenv("AI_PROVIDER", "gemini").lower()
        consent = st.checkbox(f"I consent to send the original and uploaded resolution photos to {('Google Gemini' if provider == 'gemini' else provider)} for evidence comparison.") if provider != "none" else False
        submit = st.form_submit_button("Submit authority response")
    if submit:
        try:
            photos = [_image_bytes(upload) for upload in uploads or []]
            resolution_id = submit_government_resolution(
                issue_id=issue.id, department=department, authority=authority,
                status=resolution_status, resolution_notes=notes,
                resolution_date=resolution_date.isoformat(), work_order_id=work_order,
                evidence_photos=[photo for photo in photos if photo],
            )
            st.success(f"Authority response recorded for {issue.public_id}. Government status and AI assessment remain separate.")
            model = get_model(provider)
            if issue.before_photo and photos and consent and model:
                try:
                    with st.spinner("Comparing original and authority evidence…"):
                        assessment = model.verify_resolution(issue.summary, issue.before_photo, photos)
                    save_government_verification(resolution_id, assessment)
                    record_model_run(model_name=f"{model.name} ({getattr(model, 'model', 'configured model')})",
                                     task="government_resolution_verification", validation_status=assessment.status,
                                     result=assessment.model_dump())
                    st.success(f"AI evidence assessment: {assessment.status.replace('_', ' ').title()} · {assessment.confidence:.0%} confidence.")
                except Exception as error:
                    st.warning(f"Authority response was saved; AI verification is pending because the model is unavailable: {error}")
            else:
                st.info("Authority response is saved. AI verification is pending until photos and explicit model consent are available.")
        except Exception as error:
            st.error(f"Could not save the authority response: {error}")


def _render_map(issues: list[CivicIssue], demo_mode: bool, height: int = 570) -> None:
    filters1, filters2, filters3 = st.columns([1, 1, 1])
    priority_filter = filters1.multiselect("Priority", ["High", "Medium", "Low"], default=["High", "Medium", "Low"], key=f"map-priority-{height}")
    category_filter = filters2.multiselect("Category", CATEGORIES, default=[], key=f"map-category-{height}")
    status_filter = filters3.multiselect("Status", ISSUE_STATUSES, default=ISSUE_STATUSES, key=f"map-status-{height}")
    visible = [i for i in issues if priority_label(i.priority_score) in priority_filter
               and i.status in status_filter and (not category_filter or i.category in category_filter)]
    located = [i for i in visible if i.latitude is not None and i.longitude is not None and not i.id.startswith("DEMO-")]
    if not located:
        st.info("No location-confirmed issues match these filters yet. Select a pin while reporting an issue; CivicFix never guesses coordinates.")
    center = ((located[0].latitude, located[0].longitude) if located else DEFAULT_MAP_CENTER)
    civic_map = folium.Map(location=center, zoom_start=13 if located else 11, control_scale=True, tiles="OpenStreetMap")
    for issue in located:
        color = "blue" if issue.status == "Resolved" else {"High": "red", "Medium": "orange", "Low": "green"}[priority_label(issue.priority_score)]
        safe_title = html.escape(issue.summary)
        safe_location = html.escape(issue.location_text or "Location not confirmed")
        popup = folium.Popup(f"<b>{safe_title}</b><br>{safe_location}<br>{issue.report_count} observations · Priority {issue.priority_score:.0f}", max_width=260)
        folium.Marker([issue.latitude, issue.longitude], tooltip=f"{priority_label(issue.priority_score)} priority · {issue.status}", popup=popup, icon=folium.Icon(color=color, icon="info-sign")).add_to(civic_map)
    st_folium(civic_map, height=height, use_container_width=True, key=f"civic-map-{height}", returned_objects=[])
    st.caption("OpenStreetMap base map · red high priority · orange medium · green low · blue resolved. No demo pins use fabricated coordinates.")
    if demo_mode:
        st.caption("Prototype Demo Data cards are shown separately because the sample set has no real coordinates.")


def _google_maps_key() -> str:
    return os.getenv("GOOGLE_MAPS_BROWSER_KEY", "").strip()


def _render_google_map(issues: list[CivicIssue], height: int = 520, focus: CivicIssue | None = None) -> None:
    key = _google_maps_key()
    if not key:
        st.info("Google Maps is ready to enable. Add a browser-restricted GOOGLE_MAPS_BROWSER_KEY to the server .env; this map is an OpenStreetMap fallback meanwhile.")
        _render_map(issues, demo_mode=any(issue.id.startswith("DEMO-") for issue in issues), height=height)
        return
    points = [{"id": issue.public_id or issue.id[:8], "category": issue.category,
               "summary": issue.summary, "status": issue.status,
               "priority": round(issue.priority_score), "reports": issue.report_count,
               "location": issue.location_text, "lat": issue.latitude, "lng": issue.longitude}
              for issue in issues if issue.latitude is not None and issue.longitude is not None]
    center = {"lat": focus.latitude, "lng": focus.longitude} if focus and focus.latitude is not None and focus.longitude is not None else (
        {"lat": points[0]["lat"], "lng": points[0]["lng"]} if points else {"lat": DEFAULT_MAP_CENTER[0], "lng": DEFAULT_MAP_CENTER[1]})
    payload = json.dumps(points, ensure_ascii=False).replace("<", "\\u003c")
    center_json = json.dumps(center)
    color_lookup = json.dumps({"High": "red", "Medium": "yellow", "Low": "green", "Resolved": "blue", "Partially resolved": "orange", "In progress": "purple", "Uncertain": "grey"})
    document = f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#map{{height:100%;margin:0}}.gm-style .gm-style-iw{{font:14px Arial}}</style></head><body><div id="map"></div><script>
const issues={payload};const initialCenter={center_json};const colors={color_lookup};
function initCivicMap(){{const map=new google.maps.Map(document.getElementById('map'),{{center:initialCenter,zoom:{15 if focus else 12},mapTypeControl:true,streetViewControl:false,fullscreenControl:true}});const bounds=new google.maps.LatLngBounds();let openInfo=null;
function esc(v){{return String(v||'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));}}
issues.forEach(i=>{{const p={{lat:i.lat,lng:i.lng}};const level=['Resolved','Partially resolved','In progress','Uncertain'].includes(i.status)?i.status:(i.priority>=75?'High':i.priority>=50?'Medium':'Low');const marker=new google.maps.Marker({{map,position:p,title:i.id+' · '+i.summary,icon:'https://maps.google.com/mapfiles/ms/icons/'+(colors[level]||'red')+'-dot.png'}});bounds.extend(p);const info=new google.maps.InfoWindow({{content:`<strong>${{esc(i.id)}}</strong><br>${{esc(i.summary)}}<br>${{esc(i.category)}}<br>Priority: ${{i.priority}}/100<br>Status: ${{esc(i.status)}}<br>Reports: ${{i.reports}}<br>Location: ${{esc(i.location||'Not confirmed')}}<br>Coordinates: ${{i.lat.toFixed(5)}}, ${{i.lng.toFixed(5)}}`}});marker.addListener('click',()=>{{if(openInfo)openInfo.close();info.open({{map,anchor:marker}});openInfo=info;}});}});if(issues.length>1)map.fitBounds(bounds);}}
window.gm_authFailure=()=>{{document.getElementById('map').innerText='Google Maps authorization failed. Check browser-key restrictions and enabled APIs.';}};
</script><script src="https://maps.googleapis.com/maps/api/js?key={html.escape(key, quote=True)}&callback=initCivicMap" async defer></script></body></html>"""
    components.html(document, height=height, scrolling=False)
    if not points:
        st.caption("No issue with confirmed coordinates is available to pin yet.")


def _render_issue_map_and_street_view(issue: CivicIssue) -> None:
    if issue.latitude is None or issue.longitude is None:
        st.info("Street View is unavailable at this location because the report has no confirmed coordinates.")
        return
    key = _google_maps_key()
    if not key:
        st.info("Street View is unavailable until you configure GOOGLE_MAPS_BROWSER_KEY. The issue location remains available on the OpenStreetMap fallback.")
        _render_map([issue], demo_mode=issue.id.startswith("DEMO-"), height=300)
        return
    show_key = f"show-street-view-{issue.id}"
    if not st.session_state.get(show_key):
        _render_google_map([issue], height=320, focus=issue)
        if st.button("View Street View", key=f"open-street-view-{issue.id}"):
            st.session_state[show_key] = True
            st.rerun()
        return
    left, right = st.columns(2, gap="small")
    with left:
        st.markdown("#### Civic map")
        _render_google_map([issue], height=320, focus=issue)
    with right:
        st.markdown("#### Nearby Street View")
        panorama_html = f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#pano{{height:100%;margin:0}}#message{{padding:18px;font:15px Arial;color:#334}}</style></head><body><div id="pano"><div id="message">Searching nearby Street View…</div></div><script>
function initPano(){{const position={{lat:{issue.latitude},lng:{issue.longitude}}};const service=new google.maps.StreetViewService();const pano=new google.maps.StreetViewPanorama(document.getElementById('pano'),{{visible:false,panControl:true,zoomControl:true,fullscreenControl:true}});service.getPanorama({{location:position,radius:100,source:google.maps.StreetViewSource.OUTDOOR}},(data,status)=>{{if(status==='OK'){{pano.setPano(data.location.pano);pano.setVisible(true);}}else{{document.getElementById('pano').innerHTML='<div id="message">Street View is unavailable at this location.</div>';}}}});}}
window.gm_authFailure=()=>{{document.getElementById('pano').innerHTML='<div id="message">Google Maps authorization failed. Check browser-key restrictions.</div>';}};
</script><script src="https://maps.googleapis.com/maps/api/js?key={html.escape(key, quote=True)}&callback=initPano" async defer></script></body></html>"""
        components.html(panorama_html, height=320, scrolling=False)


def _render_civic_map(issues: list[CivicIssue], demo_mode: bool) -> None:
    _header("CIVICFIX / MAP", "Civic map", "Explore location-confirmed issues by priority, category and status.")
    _render_google_map(issues)


def _render_issue_details(issue: CivicIssue, reviewer: bool, citizen_key: str, demo_mode: bool) -> None:
    if issue.id.startswith("DEMO-"):
        _html('<div class="demo-banner">Prototype Demo Data · illustrative only · no evidence images or exact coordinates</div>')
    freshness, updated_label = _freshness_label(issue.last_seen)
    _header(f"ISSUE / {issue.public_id or issue.id[:8]}", issue.summary, f"{issue.category} · {issue.location_text or 'Location not confirmed'}")
    top = st.columns([1, 1, 1, 1.3])
    top[0].metric("Priority", f"{issue.priority_score:.0f}/100", priority_label(issue.priority_score))
    top[1].metric("Reports", issue.report_count)
    top[2].metric("Confirmations", issue.confirmation_count)
    health = "Resolved" if issue.status == "Resolved" else "Critical" if issue.priority_score >= 85 else "Needs attention" if issue.priority_score >= 55 else "Monitored"
    top[3].metric("Issue health", health)
    freshness_class = "Fresh" if freshness == "Fresh" else "Aging" if freshness == "Aging" else "Stale"
    st.caption(f"Evidence freshness · {freshness_class} · {updated_label} · status source: {issue.status_source.replace('_', ' ')}")

    detail, side = st.columns([1.5, 1], gap="large")
    with detail:
        st.markdown("#### Priority breakdown")
        _priority_breakdown(issue)
        st.markdown("#### Why this matters")
        reports = list_reports_for_issue(issue.id) if not issue.id.startswith("DEMO-") else []
        explanation = next((r.draft.reasoning_summary for r in reports if r.draft.reasoning_summary), "")
        if explanation:
            st.write(explanation)
        else:
            st.caption("No model explanation is available for this issue.")

        st.markdown("#### Evidence and report history")
        own_reports = [r for r in reports if r.owner_session_key == citizen_key]
        can_view_evidence = reviewer or bool(own_reports)
        if can_view_evidence and not issue.id.startswith("DEMO-"):
            images = st.columns(3)
            evidence_items = [
                ("Original", issue.before_photo if reviewer else (own_reports[-1].photo_jpeg if own_reports else None)),
                ("Latest", issue.latest_photo if reviewer else (own_reports[0].photo_jpeg if own_reports else None)),
                ("After", issue.after_photo if reviewer else None),
            ]
            for col, (label, data) in zip(images, evidence_items):
                if data:
                    col.image(data, caption=label, use_container_width=True)
        elif reports:
            st.info("Evidence photos and other residents' report text are restricted to their owners and reviewers.")
        if reviewer and reports:
            for report in reports:
                st.markdown(f"**{report.created_at[:10]} · {report.status}:** {html.escape(report.citizen_description)}")
        st.markdown("#### Issue timeline")
        events = issue_timeline(issue.id) if not issue.id.startswith("DEMO-") else []
        if events:
            for event in events:
                st.markdown(f"**{event['at'][:10]}** · {event['event']}")
        elif issue.id.startswith("DEMO-"):
            st.caption("Sample timeline is omitted; demo dates are illustrative.")
        else:
            st.caption("No timeline events yet.")

        if issue.authority_status:
            st.markdown("#### Resolution status")
            st.write(f"**Government status:** {issue.authority_status}")
            if issue.resolution_status:
                st.write(f"**AI evidence assessment:** {issue.resolution_status.replace('_', ' ').title()} · {issue.resolution_confidence:.0%} confidence")
            else:
                st.info("Authority response received · AI evidence verification pending.")
        _render_issue_map_and_street_view(issue)

    with side:
        with st.container(border=True):
            st.markdown("#### Is this issue still present?")
            counts = confirmation_counts(issue.id) if not issue.id.startswith("DEMO-") else {}
            st.caption(f"{counts.get('still_present', 0)} still present · {counts.get('fixed', 0)} reported fixed · {counts.get('unsure', 0)} unsure")
            votes = st.columns(3)
            for col, label, kind in zip(votes, ["Still present", "Looks fixed", "Not sure"], ["still_present", "fixed", "unsure"]):
                if col.button(label, key=f"vote-{issue.id}-{kind}", disabled=issue.id.startswith("DEMO-")):
                    if confirm_issue(issue.id, citizen_key, kind):
                        st.session_state["issue_notice"] = "Your community observation was recorded."
                    else:
                        st.session_state["issue_notice"] = "You already recorded an observation for this issue in this session."
                    st.rerun()
            same, different = st.columns(2)
            if same.button("Same issue", key=f"same-{issue.id}", disabled=issue.id.startswith("DEMO-")):
                if confirm_issue(issue.id, citizen_key, "same_issue"):
                    st.session_state["issue_notice"] = "Same-issue confirmation recorded."
                else:
                    st.session_state["issue_notice"] = "You already recorded an observation for this issue in this session."
                st.rerun()
            if different.button("This is different", key=f"different-{issue.id}", disabled=issue.id.startswith("DEMO-")):
                st.session_state["active_page"] = "Scan issue"
                st.session_state["issue_notice"] = "Describe the separate issue and submit a new report."
                st.rerun()
        if not issue.id.startswith("DEMO-"):
            _route_links(issue.category)
        if reviewer and not issue.id.startswith("DEMO-"):
            with st.expander("Reviewer tools"):
                new_status = st.selectbox("Set issue status", ISSUE_STATUSES, index=ISSUE_STATUSES.index(issue.status), key=f"review-status-{issue.id}")
                if st.button("Update status", key=f"update-issue-{issue.id}"):
                    update_issue_status(issue.id, new_status)
                    st.rerun()
                other_issues = [i for i in list_issues() if i.id != issue.id]
                if other_issues:
                    selected = st.selectbox("Merge this issue into", other_issues,
                                            format_func=lambda item: f"{item.category} · {item.summary}",
                                            key=f"merge-target-{issue.id}")
                    if st.button("Merge duplicate into selected issue", key=f"merge-{issue.id}"):
                        merge_issues(issue.id, selected.id)
                        st.success("Issue observations and confirmations were merged.")
                        st.rerun()
                if issue.before_photo:
                    with st.form(f"review-verify-{issue.id}"):
                        after_upload = st.file_uploader("Upload later evidence", type=["jpg", "jpeg", "png", "webp"], key=f"review-after-{issue.id}")
                        consent = st.checkbox("I consent to sending before/after evidence to the configured model", key=f"review-consent-{issue.id}")
                        compare = st.form_submit_button("Run before/after assessment")
                    if compare:
                        if not after_upload or not consent:
                            st.error("Choose an image and consent to comparison.")
                        else:
                            try:
                                after_image = _image_bytes(after_upload)
                                model = get_model()
                                if model is None:
                                    raise RuntimeError("AI provider is disabled.")
                                with st.spinner("Comparing before and after evidence…"):
                                    assessment = model.verify_resolution(issue.summary, issue.before_photo, [after_image])
                                model_name = f"{model.name} ({getattr(model, 'model', 'configured model')})"
                                save_resolution(issue.id, after_image, assessment)
                                record_model_run(model_name=model_name, task="resolution_verification", validation_status=assessment.status, result=assessment.model_dump())
                                st.session_state["issue_notice"] = "Evidence comparison saved. It is not an official repair confirmation."
                                st.rerun()
                            except Exception as error:
                                st.error(f"Resolution comparison failed: {error}")


def _render_issues(issues: list[CivicIssue], reviewer: bool, citizen_key: str, demo_mode: bool) -> None:
    _header("CIVICFIX / ISSUES", "Issue intelligence", "One issue can contain several observations, confirmations and evidence updates.")
    notice = st.session_state.pop("issue_notice", None)
    if notice:
        st.success(notice)
    if demo_mode:
        _html('<div class="demo-banner">Prototype Demo Data is included below and labeled. Sample records are not persistent or real.</div>')
    if not issues:
        st.info("No issues to display yet.")
        return
    query = st.text_input("Search by issue or category", placeholder="Try road damage or drainage")
    categories = sorted({i.category for i in issues})
    category = st.selectbox("Category filter", ["All categories", *categories])
    filtered = [i for i in issues if (not query or query.lower() in (i.public_id + i.summary + i.category + i.location_text).lower()) and (category == "All categories" or i.category == category)]
    if not filtered:
        st.info("No issues match these filters.")
        return
    selected_id = st.selectbox("Open issue details", [i.id for i in filtered],
                               index=next((n for n, i in enumerate(filtered) if i.id == st.session_state.get("selected_issue_id")), 0),
                               format_func=lambda value: next(f"{i.public_id or i.id[:8]} · {i.summary} · {i.status}" for i in filtered if i.id == value),
                               key="issue-selector")
    st.session_state["selected_issue_id"] = selected_id
    selected = next(issue for issue in filtered if issue.id == selected_id)
    _render_issue_details(selected, reviewer, citizen_key, demo_mode)


def _render_insights(issues: list[CivicIssue], reviewer: bool) -> None:
    _header("CIVICFIX / INSIGHTS", "Operations overview", "A concise view of issue load, priority and resolution status.")
    if not issues:
        st.info("No issue data is available yet.")
        return
    open_count = sum(issue.status in ("Open", "Uncertain", "Partially resolved") for issue in issues)
    high = sum(issue.priority_score >= 75 and issue.status != "Resolved" for issue in issues)
    grouped = sum(max(0, issue.report_count - 1) for issue in issues)
    resolved = sum(issue.status == "Resolved" for issue in issues)
    kpis = st.columns(4)
    kpis[0].metric("Open issues", open_count)
    kpis[1].metric("High priority", high)
    kpis[2].metric("Duplicate observations", grouped)
    kpis[3].metric("Resolved", resolved)
    st.caption("Prototype aggregates include sample records only when Demo Mode is enabled.")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Issue categories")
        categories = Counter(issue.category for issue in issues)
        st.bar_chart(dict(categories), color="#167a55", horizontal=True)
    with right:
        st.markdown("#### Priority distribution")
        distribution = Counter(priority_label(issue.priority_score) for issue in issues)
        st.bar_chart({key: distribution.get(key, 0) for key in ["High", "Medium", "Low"]}, color="#c58018")
    st.markdown("#### Highest-priority open issues")
    for issue in [i for i in issues if i.status != "Resolved"][:5]:
        st.markdown(f"- **{issue.priority_score:.0f} · {priority_label(issue.priority_score)} · {issue.category}:** {issue.summary}")
    if not reviewer:
        st.caption("Sensitive evidence and reviewer actions require CIVICFIX_REVIEW_KEY in the server environment.")


def _render_profile(citizen_key: str) -> None:
    _header("CIVICFIX / ABOUT", "Profile & project", "Your reports are scoped to this browser session in the prototype.")
    st.markdown("#### Session profile")
    st.write(f"Local session reference · `{citizen_key[:8]}`")
    st.caption("This is not an account. Closing the browser or losing session state can disconnect you from My Reports; saved reports remain in the server database.")
    st.markdown("#### Open source")
    st.write("CivicFix AI is shared under the MIT License. The hosted AI provider has separate model terms.")
    repo = os.getenv("CIVICFIX_REPO_URL", "")
    if repo:
        st.link_button("GitHub repository", repo)
    else:
        st.caption("Repository URL is not configured. Set CIVICFIX_REPO_URL to add a verified link.")
    st.markdown("#### Technology")
    st.write("Python · Streamlit · Gemini multimodal API · Pydantic · SQLite · Google Maps/Street View (optional) · OpenStreetMap fallback")
    st.link_button("Uttar Pradesh JanSunwai portal", recommendations("Other civic issue")[1][0].url)
    st.markdown("#### Data and privacy")
    st.write("Reports, authority response details and photos are stored in the app server's SQLite database. Google Maps browser key is optional and must be restricted to your site. When Gemini is enabled, the app asks before sending report text or images for analysis. The Authority Portal is a gated prototype, not a real government integration.")
    st.toggle("Dark appearance", key="dark-theme", help="Changes the local app appearance.")


def run() -> None:
    st.set_page_config(page_title="CivicFix AI", page_icon="⌖", layout="wide", initial_sidebar_state="expanded")
    initialize()
    pending_page = st.session_state.pop("pending_page", None)
    if pending_page in PAGES:
        st.session_state["active_page"] = pending_page
    citizen_key = st.session_state.setdefault("citizen_session_key", str(uuid.uuid4()))
    st.markdown(_css(bool(st.session_state.get("dark-theme", False))), unsafe_allow_html=True)

    with st.sidebar:
        _html('<div class="brand"><div class="brand-mark">⌖</div><div><div class="brand-name">CivicFix</div><div class="brand-sub">Civic intelligence</div></div></div>')
        st.caption(JURISDICTION)
        demo_mode = st.toggle("Prototype Demo Data", key="demo-mode", help="Adds clearly labeled sample cards; nothing is written to the database.")
        st.radio("Navigation", PAGES, format_func=lambda page: f"{PAGE_ICONS[page]}   {page}", key="active_page", label_visibility="collapsed")
        st.divider()
        if os.getenv("AI_PROVIDER", "gemini").lower() == "gemini":
            st.caption("AI provider · Gemini API")
        else:
            st.caption(f"AI provider · {os.getenv('AI_PROVIDER', 'gemini')}")
        reviewer = _get_reviewer_access()
        if reviewer:
            st.success("Reviewer access enabled")

    issues = _issues(demo_mode)
    page = st.session_state.get("active_page", "Home")
    if page == "Home":
        _render_home(issues, demo_mode, citizen_key)
    elif page == "Scan issue":
        _render_scan(citizen_key, demo_mode)
    elif page == "My reports":
        _render_my_reports(citizen_key)
    elif page == "All reports":
        _render_all_reports(reviewer)
    elif page == "Civic map":
        _render_civic_map(issues, demo_mode)
    elif page == "Issues":
        _render_issues(issues, reviewer, citizen_key, demo_mode)
    elif page == "Government Resolutions":
        _render_government_resolutions(issues, reviewer, citizen_key, demo_mode)
    elif page == "Authority Portal":
        _render_authority_portal(issues, demo_mode)
    elif page == "Insights":
        _render_insights(issues, reviewer)
    else:
        _render_profile(citizen_key)
