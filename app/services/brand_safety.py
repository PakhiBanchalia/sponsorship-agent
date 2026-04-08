from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
from httpx import HTTPStatusError

from app.config import get_settings


PROMPT_TEMPLATE = """You are a brand safety analyst.

Brand brief:
{brief_text}
Brand vertical: {vertical}
Target audience: {audience}

Creator's recent video titles and descriptions:
{video_samples}

Task:
1. Identify any content that conflicts with or could embarrass the brand.
2. Consider: controversial topics, competing product endorsements, values mismatches,
   lifestyle contradictions (e.g. organic brand + steroid content), political content,
   explicit language, or anything the brand's target audience would find off-putting.
3. Be specific. Do not flag benign content.

Return ONLY this JSON, no preamble, no markdown:
{{
  "content_safety_score": 82,
  "red_flags": [
    "Creator openly discusses steroid use in videos dated within last 3 months",
    "Video titled 'Why I Don't Trust Organic Labels' directly conflicts with brand positioning"
  ],
  "summary": "Creator's content is mostly fitness-positive but has explicit steroid references that conflict with an organic brand."
}}

content_safety_score: 0 = severe brand risk, 100 = perfect brand fit.
red_flags: list of specific concerns with evidence. Empty list if none found.
If no conflicts found, return score between 75–100 and empty red_flags.
Do not hallucinate. Only flag what is directly evidenced in the video samples provided."""


async def run_brand_safety_check(
    brief_text: str,
    vertical: str,
    audience: str,
    recent_video_samples: List[Dict[str, Any]],
) -> Dict[str, Any]:
    settings = get_settings()
    if not settings.brand_safety_llm_api_key:
        return {
            "content_safety_score": None,
            "red_flags": [],
            "summary": "Brand safety check skipped: missing API key.",
            "brand_safety_status": "unchecked",
        }

    video_text = "\n".join(
        f"{index + 1}. {sample.get('title', '').strip()} — {sample.get('description_snippet', '').strip()}"
        for index, sample in enumerate(recent_video_samples[:10])
    )
    prompt = PROMPT_TEMPLATE.format(
        brief_text=brief_text,
        vertical=vertical,
        audience=audience,
        video_samples=video_text or "No samples available.",
    )

    provider = getattr(settings, "brand_safety_llm_provider", "gemini").strip().lower()
    try:
        parsed = await _generate_brand_safety_json(
            provider=provider,
            api_key=settings.brand_safety_llm_api_key,
            model=settings.brand_safety_llm_model,
            prompt=prompt,
        )
    except HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        return {
            "content_safety_score": None,
            "red_flags": [],
            "summary": f"Brand safety check failed: {detail}",
            "brand_safety_status": "unchecked",
        }
    except Exception as exc:
        return {
            "content_safety_score": None,
            "red_flags": [],
            "summary": f"Brand safety check failed: {exc}",
            "brand_safety_status": "unchecked",
        }

    red_flags = parsed.get("red_flags") or []
    score = parsed.get("content_safety_score")
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = None

    threshold = settings.brand_safety_score_threshold
    status = "clear"
    if score_value is None:
        status = "unchecked"
    elif red_flags or score_value < threshold:
        status = "flagged"

    return {
        "content_safety_score": score_value,
        "red_flags": red_flags,
        "summary": parsed.get("summary") or "",
        "brand_safety_status": status,
    }


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model response")
    return json.loads(text[start : end + 1])


async def _generate_brand_safety_json(provider: str, api_key: str, model: str, prompt: str) -> Dict[str, Any]:
    if provider == "anthropic":
        return await _generate_with_anthropic(api_key, model, prompt)
    if provider == "gemini":
        return await _generate_with_gemini(api_key, model, prompt)
    raise ValueError(f"Unsupported brand safety provider: {provider}")


async def _generate_with_anthropic(api_key: str, model: str, prompt: str) -> Dict[str, Any]:
    payload = {
        "model": model,
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    text = "".join(block.get("text", "") for block in data.get("content", []) if isinstance(block, dict))
    return _extract_json(text)


async def _generate_with_gemini(api_key: str, model: str, prompt: str) -> Dict[str, Any]:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "x-goog-api-key": api_key,
        "content-type": "application/json",
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"No Gemini candidates returned: {data}")
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    return _extract_json(text)
