from __future__ import annotations

import os
import sys
import uuid
import html
from pathlib import Path
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import folium
import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageOps
from streamlit_folium import st_folium

from civicfix.ai import get_model, local_fallback
from civicfix.issue_service import report_quality, score_report, suggest_duplicate
from civicfix.models import ComplaintDraft, ResolutionAssessment
from civicfix.priority import describe_formula, priority_label
from civicfix.routing import recommendations
from civicfix.storage import (
    confirm_issue, initialize, list_issues, list_reports_for_issue, record_model_run,
    save_report, save_resolution,
)


load_dotenv()
st.set_page_config(page_title="CivicFix AI", page_icon="🏙️", layout="wide")
initialize()

JURISDICTION, _ = recommendations("Other civic issue")
CATEGORIES = list(ComplaintDraft.model_fields["category"].annotation.__args__)
STATUSES = ["Draft", "Ready to submit", "Submitted", "In progress", "Resolved"]


def prepare_image(upload) -> bytes | None:
    if upload is None:
        return None
    image = Image.open(upload)
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((1600, 1600))
    output = BytesIO()
    image.save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()


def display_route_links(category: str) -> None:
    _, routes = recommendations(category)
    st.markdown("#### Official reporting channel")
    for route in routes:
        st.link_button(f"Open {route.authority}", route.url, use_container_width=True)
        st.caption(f"{route.channel}. {route.note}")
    st.caption("Submitting on the external portal is a separate action. Only mark this report submitted after you complete that step.")


def render_dashboard() -> None:
    issues = list_issues()
    st.subheader("Community issue dashboard")
    st.caption("The scores and statuses are prototype estimates and citizen-entered observations, not official findings.")
    confirmation_notice = st.session_state.pop("confirmation_notice", None)
    if confirmation_notice:
        st.success(confirmation_notice)
    if not issues:
        st.info("No issues saved yet. Start with the Report an issue tab.")
        return

    report_total = sum(issue.report_count for issue in issues)
    open_count = sum(issue.status in ("Open", "Partially resolved", "Uncertain") for issue in issues)
    high_count = sum(issue.priority_score >= 75 for issue in issues if issue.status != "Resolved")
    duplicate_reports = max(0, report_total - len(issues))
    metrics = st.columns(4)
    metrics[0].metric("Issue clusters", len(issues))
    metrics[1].metric("Open / uncertain", open_count)
    metrics[2].metric("High priority", high_count)
    metrics[3].metric("Grouped observations", duplicate_reports)

    located = [issue for issue in issues if issue.latitude is not None and issue.longitude is not None]
    if located:
        issue_map = folium.Map(location=(located[0].latitude, located[0].longitude), zoom_start=13, control_scale=True)
        for issue in located:
            folium.Marker(
                [issue.latitude, issue.longitude],
                tooltip=f"{priority_label(issue.priority_score)} {issue.priority_score:.0f} · {issue.status}",
                popup=f"{html.escape(issue.summary)}<br>{issue.report_count} observation(s)<br>{html.escape(issue.location_text or 'Citizen-provided coordinates')}",
            ).add_to(issue_map)
        st_folium(issue_map, height=380, use_container_width=True, returned_objects=[])
    else:
        st.caption("Map pins appear when a resident supplies coordinates. CivicFix never infers exact coordinates from a photo.")

    st.markdown("#### Prioritized issues")
    for issue in issues:
        heading = f"{priority_label(issue.priority_score)} {issue.priority_score:.0f}/100 · {issue.status} · {issue.summary}"
        with st.expander(heading):
            left, right = st.columns([2, 1])
            with left:
                st.write(f"**Category:** {issue.category} · **Reports:** {issue.report_count} · **Community confirmations:** {issue.confirmation_count}")
                st.caption(f"First reported {issue.first_seen} · Latest observation {issue.last_seen} · Location: {issue.location_text or 'Not provided'}")
                if issue.resolution_note:
                    st.info(f"AI visual assessment ({issue.resolution_status}, {issue.resolution_confidence:.0%} confidence): {issue.resolution_note}")
                photos = st.columns(3)
                for col, image_data, label in zip(
                    photos, [issue.before_photo, issue.latest_photo, issue.after_photo],
                    ["First evidence", "Latest evidence", "After evidence"],
                ):
                    if image_data:
                        col.image(image_data, caption=label, use_container_width=True)
                observations = list_reports_for_issue(issue.id)
                if observations:
                    st.markdown(f"**{len(observations)} report observation(s)**")
                    for observation in observations:
                        st.markdown(f"- **{observation.created_at[:10]}:** {observation.citizen_description}")
                if issue.before_photo:
                    with st.form(f"verify-{issue.id}"):
                        after_upload = st.file_uploader("Add a new photo for before/after assessment", type=["jpg", "jpeg", "png", "webp"], key=f"after-{issue.id}")
                        provider = os.getenv("AI_PROVIDER", "gemini").lower()
                        consent = st.checkbox(f"I agree to send the new photo and original evidence to {provider} for comparison.", key=f"after-consent-{issue.id}")
                        verify = st.form_submit_button("Compare evidence")
                    if verify:
                        if after_upload is None or not consent:
                            st.error("Add the new evidence photo and give consent before comparison.")
                        else:
                            try:
                                after_bytes = prepare_image(after_upload)
                                model = get_model(provider)
                                if model is None:
                                    assessment = ResolutionAssessment(
                                        status="uncertain", confidence=0,
                                        visible_changes=["AI is disabled; no image comparison was performed."],
                                        reasoning_summary="No AI resolution assessment was made.",
                                        limitations=["The before and after images were not compared."],
                                    )
                                    model_name = "AI disabled"
                                else:
                                    assessment = model.verify_resolution(issue.summary, issue.before_photo, after_bytes)
                                    model_name = f"{model.name} ({getattr(model, 'model', 'configured model')})"
                                save_resolution(issue.id, after_bytes, assessment)
                                record_model_run(model_name=model_name, task="resolution_verification", validation_status=assessment.status, result=assessment.model_dump())
                                st.success("Evidence assessment saved. It is not an official resolution confirmation.")
                                st.rerun()
                            except Exception as error:
                                st.error(f"Could not compare these photos: {error}")
            with right:
                st.metric("Priority", f"{issue.priority_score:.1f}/100", priority_label(issue.priority_score))
                st.caption(f"Formula: {describe_formula()}. Prototype ranking only; not official policy.")
                if st.button("I have also observed this issue", key=f"confirm-{issue.id}"):
                    session_key = st.session_state.setdefault("confirmation_session", str(uuid.uuid4()))
                    if confirm_issue(issue.id, session_key):
                        st.session_state["confirmation_notice"] = "Confirmation recorded once for this browser session."
                    else:
                        st.session_state["confirmation_notice"] = "This browser session has already confirmed the issue."
                    st.rerun()
                display_route_links(issue.category)
                reports = list_reports_for_issue(issue.id)
                if reports:
                    selected_report = reports[0]
                    with st.form(f"status-{selected_report.id}"):
                        status = st.selectbox("Citizen-entered report status", STATUSES, index=STATUSES.index(selected_report.status), key=f"status-select-{selected_report.id}")
                        reference = st.text_input("Portal reference (if submitted)", selected_report.external_reference, key=f"ref-{selected_report.id}")
                        update = st.form_submit_button("Save report status")
                        if update:
                            save_report(
                                report_id=selected_report.id, issue_id=issue.id, status=status,
                                external_reference=reference, citizen_description=selected_report.citizen_description,
                                location_text=selected_report.location_text, latitude=selected_report.latitude,
                                longitude=selected_report.longitude, photo_jpeg=None, draft=selected_report.draft,
                                priority_score=issue.priority_score,
                            )
                            st.rerun()


def render_report_form() -> None:
    st.subheader("Prepare a civic issue report")
    st.write("Describe a local issue, add a photo if available, and include a landmark or map coordinates.")

    with st.form("issue-input"):
        description = st.text_area("What is the issue?", max_chars=4000, placeholder="Example: A deep pothole has formed beside the bus stop on ...")
        location = st.text_input("Landmark or address", placeholder="A nearby landmark or street name")
        photo = st.file_uploader("Evidence photo (optional)", type=["jpg", "jpeg", "png", "webp"])
        col_lat, col_lon = st.columns(2)
        latitude = col_lat.number_input("Latitude (optional)", min_value=-90.0, max_value=90.0, value=None, format="%.6f", help="Use your map or device to enter coordinates. CivicFix does not guess them.")
        longitude = col_lon.number_input("Longitude (optional)", min_value=-180.0, max_value=180.0, value=None, format="%.6f")
        provider = os.getenv("AI_PROVIDER", "gemini").lower()
        if provider != "none":
            consent = st.checkbox(f"I agree to send this description{', photo' if photo is not None else ''} to the configured {provider} model provider for analysis.")
        else:
            consent = True
        generate = st.form_submit_button("Analyze and prepare draft", type="primary", use_container_width=True)

    if generate:
        if not description.strip():
            st.error("Describe the issue before generating a report.")
            return
        if (latitude is None) != (longitude is None):
            st.error("Enter both coordinates, or leave both blank.")
            return
        if provider != "none" and not consent:
            st.error("Consent is required to send report content to the selected AI provider.")
            return
        try:
            image_bytes = prepare_image(photo)
        except Exception:
            st.error("That image could not be read. Try a JPG or PNG photo.")
            return
        if image_bytes and len(image_bytes) > 8 * 1024 * 1024:
            st.error("The optimized image is over 8 MB. Choose a smaller photo.")
            return
        with st.spinner("Validating evidence and preparing a structured draft..."):
            try:
                model = get_model(provider)
                if model is None:
                    draft = local_fallback(description, location)
                    mode = "Local text template (AI disabled)"
                else:
                    draft = model.draft(description, location, image_bytes)
                    mode = f"{model.name} ({getattr(model, 'model', 'configured model')})"
            except Exception as error:
                st.warning(f"AI analysis was unavailable: {error} A local template will be shown as unverified and cannot pass the AI validation gate.")
                draft = local_fallback(description, location)
                mode = "Local text template fallback"
        st.session_state["draft"] = draft.model_dump()
        st.session_state["source_description"] = description.strip()
        st.session_state["location_text"] = location.strip()
        st.session_state["latitude"] = latitude
        st.session_state["longitude"] = longitude
        st.session_state["photo_jpeg"] = image_bytes
        st.session_state["draft_mode"] = mode
        st.session_state["report_id"] = None
        record_model_run(
            model_name=mode, task="issue_analysis", validation_status=draft.validation_status,
            result=draft.model_dump(),
        )

    if "draft" not in st.session_state:
        return

    st.divider()
    st.markdown("### Review and edit before saving")
    st.caption(f"Draft prepared with: {st.session_state['draft_mode']}. AI suggestions can be wrong; verify every detail.")
    current = ComplaintDraft.model_validate(st.session_state["draft"])
    if current.validation_status != "valid" or not current.is_civic_issue or current.confidence < 0.55:
        st.error(f"Evidence validation: {current.validation_status}. CivicFix will not create a complaint from uncertain or irrelevant evidence.")
        if current.reasoning_summary:
            st.write(current.reasoning_summary)
        if current.missing_information:
            st.markdown("**Please add:** " + "; ".join(current.missing_information))
        if current.follow_up_questions:
            st.markdown("**Clarification questions:** " + " ".join(current.follow_up_questions))
        st.info("Add the missing context or a clearer photo in the form above, then analyze again.")
        return

    priority_score = score_report(current)
    st.markdown("#### Evidence-grounded assessment")
    stat_cols = st.columns(5)
    stat_cols[0].metric("AI confidence", f"{current.confidence:.0%}")
    stat_cols[1].metric("Severity", f"{current.severity}/100")
    stat_cols[2].metric("Public impact", f"{current.public_impact}/100")
    stat_cols[3].metric("Safety concern", f"{current.safety_risk}/100")
    stat_cols[4].metric("Priority score", f"{priority_score:.1f}", priority_label(priority_score))
    quality = report_quality(
        current, has_photo=bool(st.session_state.get("photo_jpeg")),
        has_location=bool(st.session_state["location_text"] or st.session_state["latitude"] is not None),
    )
    st.caption(f"Evidence quality: {current.evidence_quality}/100 · Report completeness: {quality}/100 · Suggested department (unverified): {current.suggested_department}")
    st.write(current.reasoning_summary)
    possible_issue, match_score = suggest_duplicate(
        current, st.session_state["source_description"], st.session_state["location_text"],
        st.session_state["latitude"], st.session_state["longitude"],
    )
    match = next((issue for issue in list_issues() if issue.id == possible_issue), None) if possible_issue else None
    if st.session_state.get("photo_jpeg"):
        st.image(st.session_state["photo_jpeg"], caption="Photo will be stored in the local SQLite database")

    with st.form("review-draft"):
        category = st.selectbox("Issue category", CATEGORIES, index=CATEGORIES.index(current.category))
        summary = st.text_input("Summary", current.summary, max_chars=240)
        complaint = st.text_area("Complaint text", current.draft_complaint, height=170, max_chars=4000)
        obs = st.text_area("Visible evidence observations (one per line)", "\n".join(current.evidence_observations))
        missing = st.text_area("Details that may be missing (one per line)", "\n".join(current.missing_information))
        questions = st.text_area("Follow-up questions (one per line)", "\n".join(current.follow_up_questions))
        group_duplicate = False
        if match:
            st.warning(f"Possible duplicate: {match.summary} ({match.report_count} observation(s), similarity score {match_score:.2f}). Review before grouping.")
            group_duplicate = st.checkbox("Add this report as another observation of that issue", value=False)
        status = st.selectbox("Local status", ["Draft", "Ready to submit"], index=1)
        save = st.form_submit_button("Save report to SQLite", type="primary", use_container_width=True)

    if save:
        try:
            revised_data = current.model_dump()
            revised_data.update({
                "category": category,
                "summary": summary,
                "draft_complaint": complaint,
                "evidence_observations": [line.strip() for line in obs.splitlines() if line.strip()],
                "missing_information": [line.strip() for line in missing.splitlines() if line.strip()],
                "follow_up_questions": [line.strip() for line in questions.splitlines() if line.strip()],
            })
            revised = ComplaintDraft.model_validate(revised_data)
        except Exception as error:
            st.error(f"Please correct the report fields: {error}")
            return
        final_score = score_report(revised)
        duplicate_issue_id = None
        if group_duplicate:
            duplicate_issue_id, _ = suggest_duplicate(
                revised, st.session_state["source_description"], st.session_state["location_text"],
                st.session_state["latitude"], st.session_state["longitude"],
            )
        saved = save_report(
            report_id=st.session_state.get("report_id"), status=status, external_reference="",
            citizen_description=st.session_state["source_description"],
            location_text=st.session_state["location_text"],
            latitude=st.session_state["latitude"], longitude=st.session_state["longitude"],
            photo_jpeg=st.session_state["photo_jpeg"], draft=revised,
            issue_id=duplicate_issue_id, priority_score=final_score,
        )
        st.session_state["report_id"] = saved.id
        st.success(f"Saved locally as issue {(saved.issue_id or '')[:8]} with priority {final_score:.1f}/100. Report ID: {saved.id}")
        display_route_links(revised.category)


st.title("🏙️ CivicFix AI")
st.caption(f"Multimodal civic issue intelligence · {JURISDICTION}")
st.info("CivicFix validates evidence, prepares an editable report, groups selected duplicates, and assesses before/after photos. It does not file complaints or establish an official repair.")
st.caption("Portal submission statuses are citizen-entered tracking notes. Visual comparisons are AI assessments, not official inspections or resolution confirmation.")
report_tab, dashboard_tab = st.tabs(["Report an issue", "Community dashboard"])
with report_tab:
    render_report_form()
with dashboard_tab:
    render_dashboard()
