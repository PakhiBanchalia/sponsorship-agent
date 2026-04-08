from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import AutomationRun, Campaign, Creator, RunEvent
from app.schemas import AutomationRunResponse, CampaignCreateRequest, CreatorResponse, OutreachRequest, SummaryResponse
from app.services.campaign_runner import CampaignRunner
from app.services.outreach import execute_outreach, generate_email_draft


app = FastAPI(title="Creator Sponsorship Agent")
templates = Jinja2Templates(directory="app/templates")
runner = CampaignRunner()


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    await runner.resume_incomplete_campaigns()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/campaigns", response_class=HTMLResponse)
def campaigns_index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    campaigns = db.query(Campaign).filter(Campaign.status != "completed").order_by(Campaign.created_at.desc()).all()
    return templates.TemplateResponse("campaigns.html", {"request": request, "campaigns": campaigns})


@app.get("/history", response_class=HTMLResponse)
def campaigns_history(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    campaigns = db.query(Campaign).filter(Campaign.status == "completed").order_by(Campaign.updated_at.desc()).all()
    return templates.TemplateResponse("history.html", {"request": request, "campaigns": campaigns})


@app.post("/campaigns")
async def create_campaign(
    brand_name: str = Form(...),
    brief_text: str = Form(...),
    budget_min: int = Form(...),
    budget_max: int = Form(...),
    geo: str = Form(...),
    audience: str = Form(...),
    vertical: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    payload = CampaignCreateRequest(
        brand_name=brand_name,
        brief_text=brief_text,
        budget_min=budget_min,
        budget_max=budget_max,
        geo=geo,
        audience=audience,
        vertical=vertical,
    )
    campaign = Campaign(**payload.model_dump(), status="draft")
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    runner.start(campaign.id)
    return RedirectResponse(url=f"/campaigns/{campaign.id}", status_code=303)


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_dashboard(campaign_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    creators = _creator_payloads(db, campaign_id)
    runs = _run_payloads(db, campaign_id)
    return templates.TemplateResponse(
        "campaign.html",
        {
            "request": request,
            "campaign": campaign,
            "summary": _build_summary(db, campaign, creators),
            "creators": creators,
            "runs": runs,
        },
    )


@app.get("/api/campaigns/{campaign_id}")
def campaign_summary(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    creators = _creator_payloads(db, campaign_id)
    summary = _build_summary(db, campaign, creators)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse("partials/summary_card.html", {"request": request, "summary": summary, "campaign": campaign})
    return JSONResponse(summary.model_dump())


@app.get("/api/campaigns/{campaign_id}/creators")
def campaign_creators(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    payloads = _creator_payloads(db, campaign_id)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse("partials/creator_table.html", {"request": request, "creators": payloads})
    return JSONResponse([payload.model_dump() for payload in payloads])


@app.get("/api/campaigns/{campaign_id}/runs")
def campaign_runs(campaign_id: int, request: Request, db: Session = Depends(get_db)):
    payloads = _run_payloads(db, campaign_id)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse("partials/run_timeline.html", {"request": request, "runs": payloads})
    return JSONResponse([payload.model_dump() for payload in payloads])


@app.post("/api/campaigns/{campaign_id}/retry")
async def retry_campaign(campaign_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    runner.retry_failed(campaign_id)
    return JSONResponse({"status": "queued"})


@app.post("/api/campaigns/{campaign_id}/complete")
async def complete_campaign(campaign_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    runner.stop(campaign_id)
    campaign.status = "completed"
    db.commit()
    return JSONResponse({"status": "completed"})


@app.post("/api/campaigns/{campaign_id}/delete")
async def delete_campaign(campaign_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    runner.stop(campaign_id)
    db.delete(campaign)
    db.commit()
    return JSONResponse({"status": "deleted"})


@app.post("/api/campaigns/{campaign_id}/restore")
async def restore_campaign(campaign_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "completed":
        raise HTTPException(status_code=400, detail="Only completed campaigns can be restored")
    campaign.status = "ready_for_outreach"
    db.commit()
    return JSONResponse({"status": "ready_for_outreach"})


@app.get("/api/creators/{creator_id}/draft")
def creator_draft(creator_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    campaign = db.get(Campaign, creator.campaign_id)
    subject, body = generate_email_draft(campaign, creator)
    return JSONResponse({"subject": subject, "body": body})


@app.post("/api/creators/{creator_id}/outreach")
async def creator_outreach(creator_id: int, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")
    campaign = db.get(Campaign, creator.campaign_id)
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = OutreachRequest(**(await request.json()))
    else:
        form = await request.form()
        payload = OutreachRequest(mode=form.get("mode", "auto"), send_email=form.get("send_email", "true") != "false")
    result = await execute_outreach(db, campaign, creator, payload.mode, payload.send_email)
    db.commit()
    return JSONResponse(result)


def _creator_payloads(db: Session, campaign_id: int) -> List[CreatorResponse]:
    creators = db.query(Creator).filter(Creator.campaign_id == campaign_id).order_by(Creator.fit_score.desc().nullslast(), Creator.id.asc()).all()
    payloads: List[CreatorResponse] = []
    for creator in creators:
        latest_run = (
            db.query(AutomationRun)
            .filter(AutomationRun.creator_id == creator.id)
            .order_by(AutomationRun.created_at.desc())
            .first()
        )
        active_stream_run = (
            db.query(AutomationRun)
            .filter(
                AutomationRun.creator_id == creator.id,
                AutomationRun.streaming_url.isnot(None),
                AutomationRun.status.in_(["PENDING", "RUNNING"]),
            )
            .order_by(AutomationRun.created_at.desc())
            .first()
        )
        payloads.append(
            CreatorResponse(
                **{field: getattr(creator, field) for field in CreatorResponse.model_fields if hasattr(creator, field)},
                latest_run_status=active_stream_run.status if active_stream_run else (latest_run.status if latest_run else None),
                latest_streaming_url=active_stream_run.streaming_url if active_stream_run else None,
            )
        )
    return payloads


def _run_payloads(db: Session, campaign_id: int) -> List[AutomationRunResponse]:
    runs = db.query(AutomationRun).filter(AutomationRun.campaign_id == campaign_id).order_by(AutomationRun.created_at.desc()).all()
    payloads: List[AutomationRunResponse] = []
    for run in runs:
        payloads.append(
            AutomationRunResponse(
                id=run.id,
                campaign_id=run.campaign_id,
                creator_id=run.creator_id,
                phase=run.phase,
                tinyfish_run_id=run.tinyfish_run_id,
                target_url=run.target_url,
                status=run.status,
                browser_profile=run.browser_profile,
                proxy_country_code=run.proxy_country_code,
                streaming_url=run.streaming_url,
                goal_succeeded=run.goal_succeeded,
                result_json=run.result_json,
                error_json=run.error_json,
                attempt_number=run.attempt_number,
                created_at=run.created_at,
                finished_at=run.finished_at,
                events=list(run.events or []),
            )
        )
    return payloads


def _build_summary(db: Session, campaign: Campaign, creators: List[CreatorResponse]) -> SummaryResponse:
    shortlisted_count = len([creator for creator in creators if creator.status in {"shortlisted", "contact_found", "draft_ready", "form_submitted", "email_sent"}])
    last_error = None
    campaign_events = db.query(RunEvent).filter(RunEvent.event_type == "campaign").order_by(RunEvent.created_at.desc()).all()
    for event in campaign_events:
        payload = event.payload_json or {}
        if payload.get("campaign_id") == campaign.id:
            last_error = event
            break
    return SummaryResponse(
        id=campaign.id,
        brand_name=campaign.brand_name,
        status=campaign.status,
        budget_min=campaign.budget_min,
        budget_max=campaign.budget_max,
        geo=campaign.geo,
        audience=campaign.audience,
        vertical=campaign.vertical,
        creator_count=len(creators),
        shortlisted_count=shortlisted_count,
        created_at=campaign.created_at.isoformat() if isinstance(campaign.created_at, datetime) else str(campaign.created_at),
        last_error_message=last_error.message if last_error and "pipeline_error=" in last_error.message else None,
    )
