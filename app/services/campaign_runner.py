from __future__ import annotations

import asyncio
import traceback
from datetime import datetime
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, db_session
from app.models import AutomationRun, Campaign, Creator, RunEvent
from app.services.brand_safety import run_brand_safety_check
from app.services.contacts import CONTACT_DISCOVERY_PROMPT, contact_goal_succeeded, sanitize_contact_result, youtube_about_url
from app.services.discovery import DISCOVERY_PROMPT, build_discovery_runs, discovery_goal_succeeded, extract_profiles, normalize_profile_url
from app.services.enrichment import ENRICHMENT_PROMPT, enrichment_goal_succeeded, extract_contact_hints, sanitize_creator_payload
from app.services.scoring import score_creators
from app.services.tinyfish_client import TinyFishClient, interpret_run


class CampaignRunner:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = TinyFishClient()
        self.tasks: Dict[int, asyncio.Task[Any]] = {}

    async def resume_incomplete_campaigns(self) -> None:
        with db_session() as db:
            campaign_ids = [
                campaign_id
                for (campaign_id,) in db.query(Campaign.id)
                .filter(
                    Campaign.status.in_(
                        [
                            "discovering",
                            "enriching",
                            "brand_safety_check",
                            "scoring",
                            "contact_discovery",
                            "outreach_in_progress",
                        ]
                    )
                )
                .all()
            ]
        for campaign_id in campaign_ids:
            if campaign_id in self.tasks and not self.tasks[campaign_id].done():
                continue
            self.start(campaign_id)

    def start(self, campaign_id: int) -> None:
        if campaign_id in self.tasks and not self.tasks[campaign_id].done():
            return
        loop = asyncio.get_running_loop()
        self.tasks[campaign_id] = loop.create_task(self.run_campaign(campaign_id))

    def retry_failed(self, campaign_id: int) -> None:
        loop = asyncio.get_running_loop()
        self.tasks[campaign_id] = loop.create_task(self._retry_failed(campaign_id))

    def stop(self, campaign_id: int) -> None:
        task = self.tasks.get(campaign_id)
        if task and not task.done():
            task.cancel()
        self.tasks.pop(campaign_id, None)

    async def run_campaign(self, campaign_id: int) -> None:
        try:
            await self._run_discovery(campaign_id)
            await self._run_enrichment(campaign_id)
            await self._run_brand_safety(campaign_id)
            await self._run_scoring(campaign_id)
            await self._run_contact_discovery(campaign_id)
            with db_session() as db:
                campaign = db.get(Campaign, campaign_id)
                if campaign:
                    campaign.status = "ready_for_outreach"
        except Exception as exc:
            with db_session() as db:
                campaign = db.get(Campaign, campaign_id)
                if campaign:
                    campaign.status = "partial_failure"
                    self._log_campaign_note(
                        db,
                        campaign,
                        f"pipeline_error={repr(exc)}",
                        {"traceback": traceback.format_exc()},
                    )

    async def _retry_failed(self, campaign_id: int) -> None:
        await self.run_campaign(campaign_id)

    async def _run_discovery(self, campaign_id: int) -> None:
        with db_session() as db:
            existing_creators = db.query(Creator).filter(Creator.campaign_id == campaign_id).count()
            campaign = db.get(Campaign, campaign_id)
            if not campaign:
                return
            if existing_creators:
                return
            campaign.status = "discovering"

        reconciled = await self._reconcile_discovery_runs(campaign_id)
        if reconciled:
            return

        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            if not campaign:
                return
            discovery_runs = build_discovery_runs(campaign.vertical, campaign.geo)

        run_ids = await self.client.run_batch(discovery_runs)
        run_rows = []
        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            for payload, run_id in zip(discovery_runs, run_ids):
                row = AutomationRun(
                    campaign_id=campaign.id,
                    phase="discovery",
                    tinyfish_run_id=run_id,
                    target_url=payload["url"],
                    browser_profile=payload["browser_profile"],
                    goal_text=DISCOVERY_PROMPT,
                    attempt_number=1,
                )
                db.add(row)
                db.flush()
                self._log_event(db, row, "created", "Discovery run created", {"run_id": run_id})
                run_rows.append(row)

        completed_runs = await self._poll_batch_runs(
            [row.tinyfish_run_id for row in run_rows],
            "discovery",
            timeout_seconds=self.settings.tinyfish_timeout_discovery,
        )
        profiles: List[Dict[str, str]] = []
        failures: List[Tuple[AutomationRun, Dict[str, Any]]] = []

        with db_session() as db:
            run_map = {row.tinyfish_run_id: db.get(AutomationRun, row.id) for row in run_rows}
            for run_payload in completed_runs:
                run_id = run_payload.get("run_id")
                run_row = next((row for row in run_rows if row.tinyfish_run_id == run_id), None)
                if run_row is None:
                    continue
                run_row = run_map[run_id]
                self._apply_run_result(db, run_row, run_payload)
                interpreted = interpret_run(run_payload)
                if interpreted.get("goal_succeeded") and discovery_goal_succeeded(interpreted.get("data") or {}):
                    extracted = extract_profiles(interpreted["data"])
                    run_row.goal_succeeded = True
                    run_row.result_json = interpreted["data"]
                    profiles.extend(extracted)
                    self._log_event(db, run_row, "completed", f"Discovery found {len(extracted)} profiles", interpreted["data"])
                else:
                    run_row.goal_succeeded = False
                    failures.append((run_row, run_payload))
                    self._log_event(db, run_row, "failed", interpreted.get("error", "Discovery run failed"), run_payload)

        if failures:
            retry_profiles = await self._retry_discovery_failures(campaign_id, failures)
            profiles.extend(retry_profiles)

        unique_profiles: Dict[str, Dict[str, str]] = {}
        for item in profiles:
            key = normalize_profile_url(item["profile_url"])
            unique_profiles.setdefault(key, item)

        if not unique_profiles:
            with db_session() as db:
                campaign = db.get(Campaign, campaign_id)
                if campaign:
                    campaign.status = "partial_failure"
            return

        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign:
                self._log_campaign_note(db, campaign, f"discovered_profiles={len(unique_profiles)}")
            for item in unique_profiles.values():
                exists = (
                    db.query(Creator)
                    .filter(Creator.campaign_id == campaign_id, Creator.profile_url == item["profile_url"])
                    .first()
                )
                if exists:
                    continue
                db.add(
                    Creator(
                        campaign_id=campaign_id,
                        platform="youtube",
                        name=item["name"],
                        profile_url=item["profile_url"],
                        status="discovered",
                        evidence_url=item["profile_url"],
                        notes=item.get("reason") or "",
                    )
                )

    async def _reconcile_discovery_runs(self, campaign_id: int) -> bool:
        with db_session() as db:
            existing_creators = db.query(Creator).filter(Creator.campaign_id == campaign_id).count()
            if existing_creators:
                return True
            tracked_rows = (
                db.query(AutomationRun)
                .filter(AutomationRun.campaign_id == campaign_id, AutomationRun.phase == "discovery")
                .order_by(AutomationRun.created_at.asc(), AutomationRun.id.asc())
                .all()
            )
            pending_run_ids = [row.tinyfish_run_id for row in tracked_rows if row.status in {"PENDING", "RUNNING"}]

        if pending_run_ids:
            completed_runs = await self._poll_batch_runs(
                pending_run_ids,
                "discovery-reconcile",
                timeout_seconds=self.settings.tinyfish_timeout_discovery,
            )
            with db_session() as db:
                row_map = {
                    row.tinyfish_run_id: row
                    for row in db.query(AutomationRun)
                    .filter(AutomationRun.campaign_id == campaign_id, AutomationRun.phase == "discovery")
                    .all()
                }
                for run_payload in completed_runs:
                    row = row_map.get(run_payload.get("run_id"))
                    if not row:
                        continue
                    self._apply_run_result(db, row, run_payload)
                    interpreted = interpret_run(run_payload)
                    if interpreted.get("goal_succeeded") and discovery_goal_succeeded(interpreted.get("data") or {}):
                        row.goal_succeeded = True
                        row.result_json = interpreted["data"]
                        self._log_event(
                            db,
                            row,
                            "completed",
                            f"Discovery reconciliation found {len(extract_profiles(interpreted['data']))} profiles",
                            interpreted["data"],
                        )
                    else:
                        row.goal_succeeded = False
                        self._log_event(
                            db,
                            row,
                            "failed",
                            interpreted.get("error", "Discovery reconciliation failed"),
                            run_payload,
                        )

        with db_session() as db:
            rows = (
                db.query(AutomationRun)
                .filter(AutomationRun.campaign_id == campaign_id, AutomationRun.phase == "discovery")
                .order_by(AutomationRun.created_at.asc(), AutomationRun.id.asc())
                .all()
            )
            profiles: List[Dict[str, str]] = []
            for row in rows:
                payload = row.result_json or {}
                if row.goal_succeeded is False:
                    continue
                if row.goal_succeeded or (row.status == "COMPLETED" and discovery_goal_succeeded(payload)):
                    row.goal_succeeded = True
                    profiles.extend(extract_profiles(payload))

            unique_profiles: Dict[str, Dict[str, str]] = {}
            for item in profiles:
                key = normalize_profile_url(item["profile_url"])
                unique_profiles.setdefault(key, item)

            for item in unique_profiles.values():
                exists = (
                    db.query(Creator)
                    .filter(Creator.campaign_id == campaign_id, Creator.profile_url == item["profile_url"])
                    .first()
                )
                if exists:
                    continue
                db.add(
                    Creator(
                        campaign_id=campaign_id,
                        platform="youtube",
                        name=item["name"],
                        profile_url=item["profile_url"],
                        status="discovered",
                        evidence_url=item["profile_url"],
                        notes=item.get("reason") or "",
                    )
                )

            return bool(unique_profiles)

    async def _retry_discovery_failures(
        self, campaign_id: int, failures: Sequence[Tuple[AutomationRun, Dict[str, Any]]]
    ) -> List[Dict[str, str]]:
        retry_runs = []
        retry_targets = []
        for failed_row, _ in failures:
            payload = {
                "url": failed_row.target_url,
                "goal": failed_row.goal_text,
                "browser_profile": "stealth",
                "proxy_config": {"enabled": True, "country_code": self.settings.default_country_code},
                "use_vault": False,
            }
            retry_targets.append((failed_row.id, payload))
            retry_runs.append(payload)

        run_ids = await self.client.run_batch(retry_runs)
        with db_session() as db:
            for (original_id, payload), run_id in zip(retry_targets, run_ids):
                original = db.get(AutomationRun, original_id)
                retry_row = AutomationRun(
                    campaign_id=original.campaign_id,
                    creator_id=original.creator_id,
                    phase=original.phase,
                    tinyfish_run_id=run_id,
                    target_url=payload["url"],
                    browser_profile="stealth",
                    proxy_country_code=self.settings.default_country_code,
                    goal_text=payload["goal"],
                    attempt_number=2,
                )
                db.add(retry_row)
                db.flush()
                self._log_event(db, retry_row, "created", "Retry discovery run created", {"run_id": run_id})

        completed_runs = await self._poll_batch_runs(
            run_ids,
            "discovery-retry",
            timeout_seconds=self.settings.tinyfish_timeout_discovery,
        )
        profiles: List[Dict[str, str]] = []
        with db_session() as db:
            rows = db.query(AutomationRun).filter(AutomationRun.tinyfish_run_id.in_(run_ids)).all()
            row_map = {row.tinyfish_run_id: row for row in rows}
            for run_payload in completed_runs:
                row = row_map.get(run_payload.get("run_id"))
                if row is None:
                    continue
                self._apply_run_result(db, row, run_payload)
                interpreted = interpret_run(run_payload)
                if interpreted.get("goal_succeeded") and discovery_goal_succeeded(interpreted.get("data") or {}):
                    row.goal_succeeded = True
                    row.result_json = interpreted["data"]
                    extracted = extract_profiles(interpreted["data"])
                    profiles.extend(extracted)
                    self._log_event(db, row, "completed", f"Retry discovery found {len(extracted)} profiles", interpreted["data"])
                else:
                    row.goal_succeeded = False
                    self._log_event(db, row, "failed", interpreted.get("error", "Retry discovery failed"), run_payload)
        return profiles

    async def _run_enrichment(self, campaign_id: int) -> None:
        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign:
                campaign.status = "enriching"
        await self._reconcile_incomplete_phase_runs(campaign_id, "enrichment")
        with db_session() as db:
            creators = db.query(Creator).filter(Creator.campaign_id == campaign_id, Creator.status == "discovered").order_by(Creator.id.asc()).limit(12).all()

        semaphore = asyncio.Semaphore(self.settings.max_enrichment_runs)

        async def worker(creator_id: int) -> None:
            async with semaphore:
                await self._run_single_creator_phase(
                    campaign_id=campaign_id,
                    creator_id=creator_id,
                    phase="enrichment",
                    target_url_getter=lambda db, creator: creator.profile_url,
                    goal_text=ENRICHMENT_PROMPT,
                    timeout_seconds=self.settings.tinyfish_timeout_enrichment,
                )

        await asyncio.gather(*(worker(creator.id) for creator in creators))

    async def _run_brand_safety(self, campaign_id: int) -> None:
        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign:
                campaign.status = "brand_safety_check"
            creators = (
                db.query(Creator)
                .filter(Creator.campaign_id == campaign_id, Creator.status != "discovered")
                .all()
            )
            campaign_brief = {"brief_text": campaign.brief_text if campaign else "", "vertical": campaign.vertical if campaign else "", "audience": campaign.audience if campaign else ""}

        semaphore = asyncio.Semaphore(self.settings.max_brand_safety_runs)

        async def worker(creator_id: int) -> None:
            async with semaphore:
                with db_session() as db:
                    creator = db.get(Creator, creator_id)
                    if not creator:
                        return
                    if creator.brand_safety_status != "unchecked" and creator.content_safety_score is not None:
                        return
                    recent_samples = list(creator.recent_video_samples_json or [])

                result = await run_brand_safety_check(
                    brief_text=campaign_brief["brief_text"],
                    vertical=campaign_brief["vertical"],
                    audience=campaign_brief["audience"],
                    recent_video_samples=recent_samples,
                )

                with db_session() as db:
                    creator = db.get(Creator, creator_id)
                    if not creator:
                        return
                    creator.content_safety_score = result["content_safety_score"]
                    creator.red_flags_json = result["red_flags"]
                    creator.brand_safety_status = result["brand_safety_status"]
                    creator.notes = self._append_note(creator.notes, result["summary"])

        await asyncio.gather(*(worker(creator.id) for creator in creators))

    async def _run_scoring(self, campaign_id: int) -> None:
        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign:
                campaign.status = "scoring"
            creators = db.query(Creator).filter(Creator.campaign_id == campaign_id).all()
            ranked = score_creators(campaign, creators)
            for index, creator in enumerate(ranked):
                creator.status = "shortlisted" if index < 5 else creator.status

    async def _run_contact_discovery(self, campaign_id: int, retry_only: bool = False) -> None:
        with db_session() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign:
                campaign.status = "contact_discovery"
        await self._reconcile_incomplete_phase_runs(campaign_id, "contact_discovery")
        with db_session() as db:
            query = db.query(Creator).filter(Creator.campaign_id == campaign_id)
            if retry_only:
                creators = query.filter(Creator.status.in_(["failed", "manual_review"])).limit(5).all()
            else:
                creators = (
                    query.filter(Creator.status.in_(["shortlisted", "enriched", "contact_found", "manual_review"]))
                    .order_by(Creator.fit_score.desc().nullslast(), Creator.id.asc())
                    .limit(5)
                    .all()
                )
                creators = [creator for creator in creators if not creator.contact_method]

        semaphore = asyncio.Semaphore(self.settings.max_contact_runs)

        async def worker(creator_id: int) -> None:
            async with semaphore:
                await self._run_single_creator_phase(
                    campaign_id=campaign_id,
                    creator_id=creator_id,
                    phase="contact_discovery",
                    target_url_getter=lambda db, creator: youtube_about_url(creator.profile_url),
                    goal_text=CONTACT_DISCOVERY_PROMPT,
                    timeout_seconds=self.settings.tinyfish_timeout_contact,
                )

        await asyncio.gather(*(worker(creator.id) for creator in creators))

    async def _run_single_creator_phase(
        self,
        campaign_id: int,
        creator_id: int,
        phase: str,
        target_url_getter,
        goal_text: str,
        timeout_seconds: int,
    ) -> None:
        with db_session() as db:
            creator = db.get(Creator, creator_id)
            if not creator:
                return
            target_url = target_url_getter(db, creator)

        run_id = await self.client.run_async(target_url, goal_text, browser_profile="lite")
        with db_session() as db:
            row = AutomationRun(
                campaign_id=campaign_id,
                creator_id=creator_id,
                phase=phase,
                tinyfish_run_id=run_id,
                target_url=target_url,
                browser_profile="lite",
                goal_text=goal_text,
                attempt_number=1,
            )
            db.add(row)
            db.flush()
            self._log_event(db, row, "created", f"{phase} run created", {"run_id": run_id})
            row_id = row.id

        run_payload = await self._poll_single_run(run_id, timeout_seconds=timeout_seconds)
        should_retry = False
        with db_session() as db:
            row = db.get(AutomationRun, row_id)
            creator = db.get(Creator, creator_id)
            if not row or not creator:
                return
            success = self._finalize_creator_phase(db, creator, row, phase, run_payload)
            should_retry = not success

        if should_retry:
            await self._retry_creator_phase(campaign_id, creator_id, row_id, phase, timeout_seconds)

    async def _retry_creator_phase(self, campaign_id: int, creator_id: int, row_id: int, phase: str, timeout_seconds: int) -> None:
        with db_session() as db:
            row = db.get(AutomationRun, row_id)
            creator = db.get(Creator, creator_id)
            if not row or not creator:
                return
            target_url = row.target_url
            goal_text = row.goal_text
        payload_proxy = {"enabled": True, "country_code": self.settings.default_country_code}
        run_id = await self.client.run_async(target_url, goal_text, browser_profile="stealth", proxy_config=payload_proxy)
        with db_session() as db:
            row = db.get(AutomationRun, row_id)
            retry_row = AutomationRun(
                campaign_id=campaign_id,
                creator_id=creator_id,
                phase=phase,
                tinyfish_run_id=run_id,
                target_url=row.target_url,
                browser_profile="stealth",
                proxy_country_code=self.settings.default_country_code,
                goal_text=row.goal_text,
                attempt_number=2,
            )
            db.add(retry_row)
            db.flush()
            self._log_event(db, retry_row, "created", f"{phase} retry run created", {"run_id": run_id})
            retry_row_id = retry_row.id

        run_payload = await self._poll_single_run(run_id, timeout_seconds=timeout_seconds)
        with db_session() as retry_db:
            latest_row = retry_db.get(AutomationRun, retry_row_id)
            latest_creator = retry_db.get(Creator, creator_id)
            if not latest_row or not latest_creator:
                return
            success = self._finalize_creator_phase(retry_db, latest_creator, latest_row, phase, run_payload)
            if not success:
                latest_creator.status = "manual_review"

    def _finalize_creator_phase(
        self, db: Session, creator: Creator, row: AutomationRun, phase: str, run_payload: Dict[str, Any]
    ) -> bool:
        self._apply_run_result(db, row, run_payload)
        interpreted = interpret_run(run_payload)

        if phase == "enrichment":
            if interpreted.get("goal_succeeded") and enrichment_goal_succeeded(interpreted.get("data") or {}):
                payload = sanitize_creator_payload(interpreted["data"])
                creator.name = payload["name"]
                creator.handle = payload["handle"]
                creator.profile_url = normalize_profile_url(payload["profile_url"])
                creator.followers = payload["followers"]
                creator.median_views_last_10 = payload["median_views_last_10"]
                creator.region_guess = payload["region_guess"]
                creator.topic_tags_json = payload["topic_tags_json"]
                creator.recent_video_samples_json = payload["recent_video_samples_json"]
                creator.evidence_url = creator.profile_url
                creator.status = "enriched"
                hints = extract_contact_hints(payload)
                if hints:
                    creator.notes = self._append_note(creator.notes, f"contact_hints={','.join(hints)}")
                row.goal_succeeded = True
                row.result_json = interpreted["data"]
                self._log_event(db, row, "completed", "Enrichment completed", interpreted["data"])
                return True
        elif phase == "contact_discovery":
            if interpreted.get("goal_succeeded") and contact_goal_succeeded(interpreted.get("data") or {}):
                payload = sanitize_contact_result(interpreted["data"])
                creator.contact_method = payload["contact_method"]
                creator.contact_value = payload["contact_value"]
                creator.notes = self._append_note(creator.notes, payload["notes"])
                creator.status = "contact_found" if payload["contact_method"] != "none" else "manual_review"
                row.goal_succeeded = True
                row.result_json = interpreted["data"]
                self._log_event(db, row, "completed", f"Contact method: {payload['contact_method']}", interpreted["data"])
                return True

        row.goal_succeeded = False
        self._log_event(db, row, "failed", interpreted.get("error", f"{phase} failed"), run_payload)
        if creator.status not in {"contact_found", "email_sent", "form_submitted", "draft_ready"}:
            creator.status = "failed"
        return False

    async def _poll_batch_runs(self, run_ids: List[str], label: str, timeout_seconds: int) -> List[Dict[str, Any]]:
        deadline = monotonic() + timeout_seconds
        while True:
            payload = await self.client.get_runs_batch(run_ids)
            data = payload.get("data") or []
            self._persist_live_batch_statuses(run_ids, data)
            statuses = {run.get("status") for run in data}
            if data and statuses.issubset({"COMPLETED", "FAILED", "CANCELLED"}):
                return data
            if monotonic() > deadline:
                timed_out_ids = [run.get("run_id") for run in data if run.get("status") not in {"COMPLETED", "FAILED", "CANCELLED"}]
                await self._cancel_runs([run_id for run_id in timed_out_ids if run_id])
                refreshed_payload = await self.client.get_runs_batch(run_ids)
                refreshed_data = refreshed_payload.get("data") or []
                if not refreshed_data:
                    refreshed_data = data
                self._persist_live_batch_statuses(run_ids, refreshed_data)
                return self._mark_timed_out_runs(refreshed_data, timeout_seconds)
            await asyncio.sleep(5)

    async def _poll_single_run(self, run_id: str, timeout_seconds: int) -> Dict[str, Any]:
        deadline = monotonic() + timeout_seconds
        while True:
            payload = await self.client.get_run(run_id)
            self._persist_live_single_status(run_id, payload)
            status = payload.get("status")
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                return payload
            if monotonic() > deadline:
                await self.client.cancel_run(run_id)
                raise TimeoutError(f"Run {run_id} timed out after {timeout_seconds}s")
            await asyncio.sleep(5)

    async def _cancel_runs(self, run_ids: List[str]) -> None:
        for run_id in run_ids:
            try:
                await self.client.cancel_run(run_id)
            except Exception:
                continue

    @staticmethod
    def _mark_timed_out_runs(runs_payload: List[Dict[str, Any]], timeout_seconds: int) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for payload in runs_payload:
            status = payload.get("status")
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                normalized.append(payload)
                continue
            timed_out_payload = dict(payload)
            timed_out_payload["status"] = "FAILED"
            timed_out_payload["error"] = {"message": f"Timed out after {timeout_seconds}s"}
            normalized.append(timed_out_payload)
        return normalized

    async def _reconcile_incomplete_phase_runs(self, campaign_id: int, phase: str) -> None:
        with db_session() as db:
            rows = (
                db.query(AutomationRun)
                .filter(
                    AutomationRun.campaign_id == campaign_id,
                    AutomationRun.phase == phase,
                    AutomationRun.status.in_(["PENDING", "RUNNING"]),
                )
                .all()
            )
            tracked = [(row.id, row.tinyfish_run_id, row.creator_id) for row in rows]

        for row_id, run_id, creator_id in tracked:
            payload = await self.client.get_run(run_id)
            with db_session() as db:
                row = db.get(AutomationRun, row_id)
                creator = db.get(Creator, creator_id) if creator_id else None
                if not row:
                    continue
                self._apply_run_result(db, row, payload)
                if creator and payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
                    success = self._finalize_creator_phase(db, creator, row, phase, payload)
                    if not success and row.attempt_number == 1:
                        pass

    def _apply_run_result(self, db: Session, row: AutomationRun, payload: Dict[str, Any]) -> None:
        row.status = str(payload.get("status") or row.status)
        row.streaming_url = payload.get("streaming_url") or row.streaming_url
        row.result_json = payload.get("result") if isinstance(payload.get("result"), dict) else row.result_json
        row.error_json = payload.get("error") if isinstance(payload.get("error"), dict) else row.error_json
        if row.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            row.finished_at = datetime.utcnow()
        self._log_event(db, row, "poll", f"Run status: {row.status}", {"streaming_url": row.streaming_url})

    def _persist_live_single_status(self, run_id: str, payload: Dict[str, Any]) -> None:
        with db_session() as db:
            row = db.query(AutomationRun).filter(AutomationRun.tinyfish_run_id == run_id).order_by(AutomationRun.id.desc()).first()
            if row:
                self._apply_run_result(db, row, payload)

    def _persist_live_batch_statuses(self, run_ids: List[str], runs_payload: List[Dict[str, Any]]) -> None:
        if not run_ids:
            return
        with db_session() as db:
            rows = db.query(AutomationRun).filter(AutomationRun.tinyfish_run_id.in_(run_ids)).all()
            by_run_id = {row.tinyfish_run_id: row for row in rows}
            for payload in runs_payload:
                run_id = payload.get("run_id")
                row = by_run_id.get(run_id)
                if row:
                    self._apply_run_result(db, row, payload)

    def _log_event(self, db: Session, row: AutomationRun, event_type: str, message: str, payload: Optional[Dict[str, Any]]) -> None:
        db.add(
            RunEvent(
                automation_run_id=row.id,
                event_type=event_type,
                message=message,
                payload_json=payload,
            )
        )

    def _log_campaign_note(self, db: Session, campaign: Campaign, note: str, payload: Optional[Dict[str, Any]] = None) -> None:
        db.add(
            RunEvent(
                automation_run_id=None,
                event_type="campaign",
                message=note,
                payload_json={"campaign_id": campaign.id, **(payload or {})},
            )
        )

    @staticmethod
    def _append_note(notes: Optional[str], text: str) -> str:
        if not notes:
            return text
        return f"{notes}; {text}"
