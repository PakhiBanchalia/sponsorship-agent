from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class CampaignCreateRequest(BaseModel):
    brand_name: str
    brief_text: str
    budget_min: int
    budget_max: int
    geo: str
    audience: str
    vertical: str


class CreatorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    platform: str
    name: str
    handle: Optional[str] = None
    profile_url: str
    followers: Optional[int] = None
    median_views_last_10: Optional[int] = None
    region_guess: Optional[str] = None
    topic_tags_json: List[str]
    recent_video_samples_json: List[Dict[str, Any]]
    contact_method: Optional[str] = None
    contact_value: Optional[str] = None
    fit_score: Optional[float] = None
    content_safety_score: Optional[float] = None
    red_flags_json: List[str]
    brand_safety_status: str
    estimated_cost_low: Optional[float] = None
    estimated_cost_high: Optional[float] = None
    expected_reach: Optional[int] = None
    status: str
    evidence_url: Optional[str] = None
    notes: Optional[str] = None
    latest_run_status: Optional[str] = None
    latest_streaming_url: Optional[str] = None


class OutreachRequest(BaseModel):
    mode: Literal["form", "email", "auto"] = "auto"
    send_email: bool = True


class OutreachResult(BaseModel):
    status: str
    contact_method: Optional[str] = None
    contact_value: Optional[str] = None
    message_preview: Optional[str] = None
    streaming_url: Optional[str] = None
    provider_message_id: Optional[str] = None


class SummaryResponse(BaseModel):
    id: int
    brand_name: str
    status: str
    budget_min: int
    budget_max: int
    geo: str
    audience: str
    vertical: str
    creator_count: int
    shortlisted_count: int
    created_at: str
    last_error_message: Optional[str] = None


class RunEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    automation_run_id: int
    event_type: str
    message: str
    payload_json: Optional[Dict[str, Any]] = None
    created_at: Any


class AutomationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    creator_id: Optional[int] = None
    phase: str
    tinyfish_run_id: str
    target_url: str
    status: str
    browser_profile: str
    proxy_country_code: Optional[str] = None
    streaming_url: Optional[str] = None
    goal_succeeded: Optional[bool] = None
    result_json: Optional[Dict[str, Any]] = None
    error_json: Optional[Dict[str, Any]] = None
    attempt_number: int
    created_at: Any
    finished_at: Any = None
    events: List[RunEventResponse] = []
