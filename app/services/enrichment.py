from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional


ENRICHMENT_PROMPT = """You are extracting public data from a YouTube channel page.

1. Navigate to the channel's main page and the About/Links tab.
2. Extract the fields listed below from what is publicly visible.
3. Do not click any subscribe, like, or comment buttons.
4. Do not log in or attempt to access private data.
5. Navigate to the Videos tab of the channel.
6. Extract the title and description (first 200 characters) of the 10 most recent public videos.
   Do not click into individual videos — use what is visible in the video grid or list.
   If descriptions are not visible in the grid, extract titles only.

Stop after extracting all visible fields and the 10 most recent video samples.
Only check individual video pages for recent_views if view counts are not visible in the grid.

Return ONLY this JSON, no preamble, no markdown:
{
  "creator": {
    "name": "FitWithJane",
    "handle": "@fitwithJane",
    "platform": "youtube",
    "profile_url": "https://www.youtube.com/@fitwithJane",
    "followers": 210000,
    "recent_views": [145000, 98000, 210000, 87000, 130000, 176000, 91000, 205000, 118000, 143000],
    "median_views_last_10": 136500,
    "region_guess": "US",
    "topic_tags": ["fitness", "weightlifting", "nutrition"],
    "contact_hints": ["jane@fitwithJane.com", "https://linktr.ee/fitwithJane"],
    "recent_video_samples": [
      {
        "title": "I Tried Going Steroid-Free for 30 Days",
        "description_snippet": "After years of enhanced training, I decided to document what happens when..."
      }
    ],
    "confidence": "high"
  }
}

Rules:
- followers: integer only, no commas or symbols. Use null if not visible.
- recent_views: list of up to 10 most recent public video view counts, integers only. Empty list if not visible.
- median_views_last_10: compute the median of recent_views. Use null if recent_views is empty.
- region_guess: infer from language, location mentions, or channel description. Use "unknown" if unclear.
- contact_hints: any visible email addresses or link-in-bio URLs. Empty list if none found.
- recent_video_samples: up to 10 items. description_snippet is first 200 chars or empty string if not visible.
- confidence: "high" if followers and recent_views both found, "medium" if one is missing, "low" if both missing.
- Do not hallucinate missing data. Use null or empty values when unknown."""


def enrichment_goal_succeeded(result: Dict[str, Any]) -> bool:
    creator = result.get("creator")
    return isinstance(creator, dict) and bool(creator.get("profile_url"))


def sanitize_creator_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    creator = dict(result.get("creator") or {})
    recent_views = [int(v) for v in creator.get("recent_views") or [] if isinstance(v, (int, float, str)) and str(v).isdigit()]
    median_views = creator.get("median_views_last_10")
    if median_views is None and recent_views:
        median_views = int(median(recent_views))
    samples = []
    for sample in creator.get("recent_video_samples") or []:
        if not isinstance(sample, dict):
            continue
        samples.append(
            {
                "title": str(sample.get("title") or "").strip(),
                "description_snippet": str(sample.get("description_snippet") or "").strip(),
            }
        )
    return {
        "name": str(creator.get("name") or "Unknown Creator").strip(),
        "handle": str(creator.get("handle") or "").strip() or None,
        "platform": "youtube",
        "profile_url": str(creator.get("profile_url") or "").strip(),
        "followers": _safe_int(creator.get("followers")),
        "median_views_last_10": _safe_int(median_views),
        "region_guess": str(creator.get("region_guess") or "unknown").strip() or "unknown",
        "topic_tags_json": [str(tag).strip() for tag in creator.get("topic_tags") or [] if str(tag).strip()],
        "recent_video_samples_json": samples[:10],
        "contact_hints": [str(tag).strip() for tag in creator.get("contact_hints") or [] if str(tag).strip()],
        "notes": f"confidence={creator.get('confidence') or 'unknown'}",
    }


def extract_contact_hints(payload: Dict[str, Any]) -> List[str]:
    return list(payload.get("contact_hints") or [])


def _safe_int(value: Optional[Any]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
