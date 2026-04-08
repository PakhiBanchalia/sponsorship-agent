from __future__ import annotations

from typing import Any, Dict


CONTACT_DISCOVERY_PROMPT = """You are finding public contact information for a YouTube creator who accepts brand sponsorships.

1. Check the creator's YouTube About/Links tab for any email addresses or external URLs.
2. If a Linktree or similar link-in-bio URL is visible, navigate to it and look for a business email or contact form.
3. If a personal website URL is visible, navigate to it and look for a Contact or Sponsorship page.

Stop after finding one valid contact method. Do not sign up for newsletters or click purchase buttons.
Only collect publicly visible contact information. Do not attempt to access DMs or private messages.

Return ONLY this JSON, no preamble, no markdown:
{
  "contact_method": "email",
  "contact_value": "jane@fitwithJane.com",
  "contact_page_url": "https://linktr.ee/fitwithJane",
  "notes": "Business email found on Linktree page"
}

Valid contact_method values: "email", "form", "linktree", "none"
If no contact method found, return: {"contact_method": "none", "contact_value": null, "contact_page_url": null, "notes": "No public contact found"}"""


def contact_goal_succeeded(result: Dict[str, Any]) -> bool:
    return isinstance(result, dict) and result.get("contact_method") in {"email", "form", "linktree", "none"}


def sanitize_contact_result(result: Dict[str, Any]) -> Dict[str, Any]:
    method = str(result.get("contact_method") or "none").strip().lower()
    value = result.get("contact_value")
    page_url = result.get("contact_page_url")
    notes = str(result.get("notes") or "").strip()

    if method == "form":
        contact_value = str(page_url or value or "").strip() or None
    elif method == "linktree":
        contact_value = str(page_url or value or "").strip() or None
    else:
        contact_value = str(value or "").strip() or None

    return {
        "contact_method": method,
        "contact_value": contact_value,
        "contact_page_url": str(page_url or "").strip() or None,
        "notes": notes,
    }


def youtube_about_url(profile_url: str) -> str:
    clean = profile_url.rstrip("/")
    if clean.endswith("/about"):
        return clean
    return f"{clean}/about"
