from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True)
    brand_name = Column(String(255), nullable=False)
    brief_text = Column(Text, nullable=False)
    budget_min = Column(Integer, nullable=False)
    budget_max = Column(Integer, nullable=False)
    geo = Column(String(100), nullable=False)
    audience = Column(String(255), nullable=False)
    vertical = Column(String(255), nullable=False)
    status = Column(String(64), nullable=False, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    creators = relationship("Creator", back_populates="campaign", cascade="all, delete-orphan")
    runs = relationship("AutomationRun", back_populates="campaign", cascade="all, delete-orphan")
    outreach_messages = relationship("OutreachMessage", back_populates="campaign", cascade="all, delete-orphan")


class Creator(Base):
    __tablename__ = "creators"

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    platform = Column(String(50), nullable=False, default="youtube")
    name = Column(String(255), nullable=False)
    handle = Column(String(255), nullable=True)
    profile_url = Column(String(1024), nullable=False)
    followers = Column(Integer, nullable=True)
    median_views_last_10 = Column(Integer, nullable=True)
    region_guess = Column(String(100), nullable=True)
    topic_tags_json = Column(JSON, nullable=False, default=list)
    recent_video_samples_json = Column(JSON, nullable=False, default=list)
    contact_method = Column(String(50), nullable=True)
    contact_value = Column(String(1024), nullable=True)
    fit_score = Column(Float, nullable=True)
    content_safety_score = Column(Float, nullable=True)
    red_flags_json = Column(JSON, nullable=False, default=list)
    brand_safety_status = Column(String(32), nullable=False, default="unchecked")
    estimated_cost_low = Column(Float, nullable=True)
    estimated_cost_high = Column(Float, nullable=True)
    expected_reach = Column(Integer, nullable=True)
    status = Column(String(64), nullable=False, default="discovered")
    evidence_url = Column(String(1024), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    campaign = relationship("Campaign", back_populates="creators")
    runs = relationship("AutomationRun", back_populates="creator")
    outreach_messages = relationship("OutreachMessage", back_populates="creator")


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("creators.id"), nullable=True)
    phase = Column(String(64), nullable=False)
    tinyfish_run_id = Column(String(255), nullable=False)
    target_url = Column(String(1024), nullable=False)
    status = Column(String(64), nullable=False, default="PENDING")
    browser_profile = Column(String(32), nullable=False, default="lite")
    proxy_country_code = Column(String(8), nullable=True)
    streaming_url = Column(String(1024), nullable=True)
    goal_text = Column(Text, nullable=False)
    result_json = Column(JSON, nullable=True)
    error_json = Column(JSON, nullable=True)
    goal_succeeded = Column(Boolean, nullable=True)
    attempt_number = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    campaign = relationship("Campaign", back_populates="runs")
    creator = relationship("Creator", back_populates="runs")
    events = relationship("RunEvent", back_populates="automation_run", cascade="all, delete-orphan")


class RunEvent(Base):
    __tablename__ = "run_events"

    id = Column(Integer, primary_key=True)
    automation_run_id = Column(Integer, ForeignKey("automation_runs.id"), nullable=True)
    event_type = Column(String(64), nullable=False)
    message = Column(Text, nullable=False)
    payload_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    automation_run = relationship("AutomationRun", back_populates="events")


class OutreachMessage(Base):
    __tablename__ = "outreach_messages"

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    creator_id = Column(Integer, ForeignKey("creators.id"), nullable=False)
    channel = Column(String(32), nullable=False)
    contact_value = Column(String(1024), nullable=True)
    subject = Column(String(255), nullable=False)
    body_text = Column(Text, nullable=False)
    status = Column(String(64), nullable=False)
    provider_message_id = Column(String(255), nullable=True)
    streaming_url = Column(String(1024), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    campaign = relationship("Campaign", back_populates="outreach_messages")
    creator = relationship("Creator", back_populates="outreach_messages")
