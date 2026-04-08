from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AutomationRun, Campaign, Creator, OutreachMessage, RunEvent
from app.services.email_sender import send_email
from app.services.tinyfish_client import TinyFishClient, interpret_run


FORM_OUTREACH_PROMPT = """You are submitting a sponsorship inquiry form on behalf of {brand_name}.

The form is at: {contact_page_url}

1. Navigate to the contact/sponsorship form.
2. Fill in the form with the information below. Use natural language mapping — match the meaning of each field, not exact labels.
3. If a cookie or consent banner appears, close it first.
4. Submit the form only once. Do not click submit more than once.
5. After submitting, note any confirmation message, reference number, or success indicator shown on the page.

Form content to submit:
- Name / Company: {brand_name}
- Contact email: {sender_email}
- Message: {message_body}

Do not fill in payment, credit card, or any financial fields. If the form requires login, stop and return status "failed".

Return ONLY this JSON, no preamble, no markdown:
{{
  "status": "submitted",
  "confirmation_text": "Thank you for reaching out! We'll be in touch within 5 business days.",
  "submitted_fields": {{"name": "{brand_name}", "email": "{sender_email}", "message": "..."}},
  "notes": "Form submitted successfully, confirmation displayed"
}}

Valid status values: "submitted", "draft_ready", "failed"
Use "draft_ready" if the form was found but could not be submitted (e.g. CAPTCHA blocking).
Use "failed" if no form was found or login was required."""


def generate_email_draft(campaign: Campaign, creator: Creator) -> Tuple[str, str]:
    subject = f"{campaign.brand_name} x {creator.name} sponsorship idea"
    budget_range = f"${campaign.budget_min:,}–${campaign.budget_max:,}"
    topic_tags = [tag for tag in (creator.topic_tags_json or []) if tag][:3]
    topic_line = ", ".join(topic_tags) if topic_tags else campaign.vertical
    recent_samples = creator.recent_video_samples_json or []
    latest_title = ""
    if recent_samples and isinstance(recent_samples[0], dict):
        latest_title = str(recent_samples[0].get("title") or "").strip()

    performance_line = ""
    if creator.median_views_last_10:
        performance_line = f"Your recent videos are landing around {creator.median_views_last_10:,} median views, which makes the reach profile especially attractive for this launch. "

    content_reference = ""
    if latest_title:
        content_reference = f'We especially liked how your recent content shows up in pieces like "{latest_title}". '

    geo_line = f"Your audience fit in {creator.region_guess or campaign.geo} also lines up well with what we need. "

    body = (
        f"Hi {creator.name},\n\n"
        f"I'm reaching out from {campaign.brand_name}. We think your YouTube content is a strong fit for our "
        f"{campaign.vertical} campaign targeting {campaign.audience} in {campaign.geo}.\n\n"
        f"Your channel stands out because of its focus on {topic_line}. "
        f"{content_reference}{performance_line}{geo_line}\n\n"
        f"We'd love to explore one sponsorship integration that feels native to your channel rather than overly scripted. "
        f"Our current budget range is {budget_range}, and we'd shape the package around the style your audience already responds to.\n\n"
        "If you're open to sponsorships, I'd love to share the brief and discuss timelines.\n\n"
        f"Best,\n{campaign.brand_name}\nReply-to: {get_settings().gmail_from_address or 'your-email@example.com'}"
    )
    return subject, body


def outreach_goal_succeeded(result: Dict[str, Any]) -> bool:
    return result.get("status") in {"submitted", "draft_ready"}


def sanitize_outreach_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": str(result.get("status") or "failed"),
        "confirmation_text": str(result.get("confirmation_text") or "").strip(),
        "submitted_fields": result.get("submitted_fields") or {},
        "notes": str(result.get("notes") or "").strip(),
    }


def build_form_prompt(campaign: Campaign, creator: Creator) -> str:
    _, body = generate_email_draft(campaign, creator)
    return FORM_OUTREACH_PROMPT.format(
        brand_name=campaign.brand_name,
        contact_page_url=creator.contact_value or creator.profile_url,
        sender_email=get_settings().gmail_from_address or "your-email@example.com",
        message_body=body,
    )


def can_send_outreach(db: Session, creator: Creator) -> bool:
    recent_cutoff = datetime.utcnow() - timedelta(days=7)
    if creator.contact_value:
        existing = (
            db.query(OutreachMessage)
            .filter(
                OutreachMessage.contact_value == creator.contact_value,
                OutreachMessage.sent_at.isnot(None),
                OutreachMessage.sent_at > recent_cutoff,
                OutreachMessage.status.in_(["email_sent", "form_submitted"]),
            )
            .order_by(desc(OutreachMessage.sent_at))
            .first()
        )
        if existing:
            return False

    campaign_count = (
        db.query(OutreachMessage)
        .filter(
            OutreachMessage.campaign_id == creator.campaign_id,
            OutreachMessage.status.in_(["email_sent", "form_submitted"]),
        )
        .count()
    )
    return campaign_count < 2


async def send_creator_email(db: Session, campaign: Campaign, creator: Creator, send_email_now: bool) -> OutreachMessage:
    subject, body = generate_email_draft(campaign, creator)
    message = OutreachMessage(
        campaign_id=campaign.id,
        creator_id=creator.id,
        channel="email",
        contact_value=creator.contact_value,
        subject=subject,
        body_text=body,
        status="draft_ready",
    )
    db.add(message)
    db.flush()

    if not send_email_now or not get_settings().allow_email_send:
        creator.status = "draft_ready"
        return message

    try:
        result = await send_email(creator.contact_value or "", subject, body)
        message.provider_message_id = result["provider_message_id"]
        message.status = "email_sent"
        message.sent_at = datetime.utcnow()
        creator.status = "email_sent"
    except Exception as exc:
        message.status = "draft_ready"
        creator.status = "draft_ready"
        creator.notes = _append_note(creator.notes, f"smtp_error={exc}")
    return message


def _append_note(notes: Optional[str], text: str) -> str:
    if not notes:
        return text
    return f"{notes}; {text}"


async def execute_outreach(
    db: Session,
    campaign: Campaign,
    creator: Creator,
    mode: str,
    send_email_now: bool,
) -> Dict[str, Any]:
    if not can_send_outreach(db, creator):
        creator.status = "manual_review"
        return {"status": "skipped", "message_preview": "Recent outreach already exists for this contact."}

    if mode == "email" or (mode == "auto" and creator.contact_method == "email"):
        message = await send_creator_email(db, campaign, creator, send_email_now)
        campaign.status = "completed" if message.status == "email_sent" else "ready_for_outreach"
        return {
            "status": message.status,
            "contact_method": "email",
            "contact_value": creator.contact_value,
            "message_preview": message.body_text,
            "provider_message_id": message.provider_message_id,
            "streaming_url": None,
        }

    if creator.contact_method not in {"form", "linktree"} and mode != "form":
        creator.status = "manual_review"
        return {"status": "failed", "contact_method": creator.contact_method, "message_preview": "No form surface found."}

    client = TinyFishClient()
    prompt = build_form_prompt(campaign, creator)
    campaign.status = "outreach_in_progress"
    run_id = await client.run_async(
        creator.contact_value or creator.profile_url,
        prompt,
        browser_profile="lite",
    )
    run_row = AutomationRun(
        campaign_id=campaign.id,
        creator_id=creator.id,
        phase="outreach",
        tinyfish_run_id=run_id,
        target_url=creator.contact_value or creator.profile_url,
        browser_profile="lite",
        goal_text=prompt,
        attempt_number=1,
    )
    db.add(run_row)
    db.flush()
    db.add(RunEvent(automation_run_id=run_row.id, event_type="created", message="Outreach run created", payload_json={"run_id": run_id}))

    payload = await _poll_outreach_run(client, run_id)
    result = interpret_run(payload)
    run_row.status = payload.get("status", run_row.status)
    run_row.streaming_url = payload.get("streaming_url") or run_row.streaming_url
    run_row.finished_at = datetime.utcnow()
    if isinstance(payload.get("result"), dict):
        run_row.result_json = payload["result"]
    if isinstance(payload.get("error"), dict):
        run_row.error_json = payload["error"]
    db.add(RunEvent(automation_run_id=run_row.id, event_type="poll", message=f"Run status: {run_row.status}", payload_json={"streaming_url": run_row.streaming_url}))

    if result.get("goal_succeeded") and outreach_goal_succeeded(result.get("data") or {}):
        clean = sanitize_outreach_result(result["data"])
        message = OutreachMessage(
            campaign_id=campaign.id,
            creator_id=creator.id,
            channel="form",
            contact_value=creator.contact_value,
            subject=f"{campaign.brand_name} form outreach",
            body_text=generate_email_draft(campaign, creator)[1],
            status="form_submitted" if clean["status"] == "submitted" else "draft_ready",
            streaming_url=run_row.streaming_url,
            sent_at=datetime.utcnow() if clean["status"] == "submitted" else None,
        )
        db.add(message)
        creator.status = "form_submitted" if clean["status"] == "submitted" else "draft_ready"
        campaign.status = "completed" if clean["status"] == "submitted" else "ready_for_outreach"
        run_row.goal_succeeded = True
        db.add(RunEvent(automation_run_id=run_row.id, event_type="completed", message=f"Outreach status: {clean['status']}", payload_json=clean))
        return {
            "status": message.status,
            "contact_method": creator.contact_method,
            "contact_value": creator.contact_value,
            "message_preview": message.body_text,
            "streaming_url": run_row.streaming_url,
            "provider_message_id": None,
        }

    run_row.goal_succeeded = False
    creator.status = "failed"
    campaign.status = "ready_for_outreach"
    db.add(RunEvent(automation_run_id=run_row.id, event_type="failed", message=result.get("error", "Outreach failed"), payload_json=payload))
    return {
        "status": "failed",
        "contact_method": creator.contact_method,
        "contact_value": creator.contact_value,
        "message_preview": None,
        "streaming_url": run_row.streaming_url,
        "provider_message_id": None,
    }


async def _poll_outreach_run(client: TinyFishClient, run_id: str) -> Dict[str, Any]:
    while True:
        payload = await client.get_run(run_id)
        if payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
            return payload
        await asyncio.sleep(5)
