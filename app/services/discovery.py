from __future__ import annotations

from typing import Dict, List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


DISCOVERY_PROMPT = """You are searching for YouTube fitness creators who accept brand sponsorships.

1. Look at the current search results page.
2. Identify up to 5 individual YouTube creator profiles (not agencies, not brands, not media companies).
3. For each creator, extract their YouTube channel URL.

Stop after finding 5 creators. Do not click pagination or Load More.
Ignore any result that is a brand, agency, publication, or YouTube channel with no individual person visible.
If a creator has no visible YouTube channel URL, skip them.

Return ONLY this JSON, no preamble, no markdown:
{
  "profiles": [
    {
      "platform": "youtube",
      "name": "FitWithJane",
      "profile_url": "https://www.youtube.com/@fitwithJane",
      "reason": "Individual fitness creator, 200k subscribers, posts workout videos"
    }
  ]
}

If no valid creators are found, return: {"profiles": []}"""


def build_discovery_runs(vertical: str, geo: str) -> List[Dict[str, str]]:
    google_query = f"fitness YouTubers sponsorship contact {vertical} {geo}"
    youtube_query = f"fitness {vertical} {geo}"
    return [
        {
            "url": f"https://www.google.com/search?{urlencode({'q': google_query})}",
            "goal": DISCOVERY_PROMPT,
            "browser_profile": "lite",
            "use_vault": False,
        },
        {
            "url": f"https://www.youtube.com/results?{urlencode({'search_query': youtube_query})}",
            "goal": DISCOVERY_PROMPT,
            "browser_profile": "lite",
            "use_vault": False,
        },
    ]


def normalize_profile_url(profile_url: str) -> str:
    parsed = urlparse(profile_url.strip())
    clean_query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=False) if key not in {"pp", "si"}]
    )
    normalized = parsed._replace(query=clean_query, fragment="")
    final_url = urlunparse(normalized).rstrip("/")
    return final_url.lower()


def extract_profiles(result: Dict[str, object]) -> List[Dict[str, str]]:
    raw_profiles = result.get("profiles") or []
    profiles = []
    for item in raw_profiles:
        if not isinstance(item, dict):
            continue
        profile_url = str(item.get("profile_url") or "").strip()
        if not profile_url:
            continue
        profiles.append(
            {
                "platform": "youtube",
                "name": str(item.get("name") or "").strip() or "Unknown Creator",
                "profile_url": normalize_profile_url(profile_url),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return profiles


def discovery_goal_succeeded(result: Dict[str, object]) -> bool:
    return isinstance(result.get("profiles"), list)
