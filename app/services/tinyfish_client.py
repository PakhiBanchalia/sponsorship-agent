from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings


class TinyFishClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.tinyfish_api_base.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.settings.tinyfish_api_key,
        }

    async def _request(self, method: str, path: str, json_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                json=json_payload,
            )
            response.raise_for_status()
            return response.json()

    async def run_async(
        self,
        url: str,
        goal: str,
        browser_profile: str = "lite",
        proxy_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "url": url,
            "goal": goal,
            "browser_profile": browser_profile,
            "use_vault": False,
        }
        if proxy_config:
            payload["proxy_config"] = proxy_config
        data = await self._request("POST", "/automation/run-async", payload)
        run_id = data.get("run_id")
        if not run_id:
            raise RuntimeError(f"Missing run_id in TinyFish response: {data}")
        return run_id

    async def run_batch(self, runs: List[Dict[str, Any]]) -> List[str]:
        payload = {"runs": runs}
        data = await self._request("POST", "/automation/run-batch", payload)
        run_ids = data.get("run_ids") or []
        if not run_ids:
            raise RuntimeError(f"Missing run_ids in TinyFish batch response: {data}")
        return run_ids

    async def get_run(self, run_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/runs/{run_id}")

    async def get_runs_batch(self, run_ids: List[str]) -> Dict[str, Any]:
        return await self._request("POST", "/runs/batch", {"run_ids": run_ids})

    async def cancel_run(self, run_id: str) -> Dict[str, Any]:
        return await self._request("POST", f"/runs/{run_id}/cancel", {})

    async def list_runs(self) -> Dict[str, Any]:
        return await self._request("GET", "/runs")


def interpret_run(run: Dict[str, Any]) -> Dict[str, Any]:
    status = run.get("status")

    if status == "COMPLETED":
        result = run.get("result") or {}
        if isinstance(result, dict) and (result.get("status") == "failure" or result.get("error")):
            return {
                "infrastructure_ok": True,
                "goal_succeeded": False,
                "error": result.get("reason") or result.get("error") or "Goal not achieved",
                "retryable": True,
                "data": None,
                "streaming_url": run.get("streaming_url"),
            }
        return {
            "infrastructure_ok": True,
            "goal_succeeded": True,
            "data": result,
            "streaming_url": run.get("streaming_url"),
        }

    if status == "FAILED":
        error = run.get("error") or {}
        message = error.get("message", "Infrastructure failure") if isinstance(error, dict) else str(error)
        return {
            "infrastructure_ok": False,
            "goal_succeeded": False,
            "error": message,
            "retryable": True,
            "data": None,
            "streaming_url": run.get("streaming_url"),
        }

    if status in ("PENDING", "RUNNING"):
        return {
            "infrastructure_ok": None,
            "goal_succeeded": None,
            "pending": True,
            "streaming_url": run.get("streaming_url"),
        }

    return {
        "infrastructure_ok": False,
        "goal_succeeded": False,
        "error": f"Unexpected status: {status}",
        "retryable": False,
        "data": None,
        "streaming_url": run.get("streaming_url"),
    }
