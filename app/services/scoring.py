from __future__ import annotations

from typing import Iterable, List, Optional, Set

from app.models import Campaign, Creator


def score_creators(campaign: Campaign, creators: List[Creator]) -> List[Creator]:
    engagement_ratios = []
    for creator in creators:
        if creator.followers and creator.median_views_last_10:
            engagement_ratios.append(creator.median_views_last_10 / max(creator.followers, 1))
    max_ratio = max(engagement_ratios) if engagement_ratios else 0

    for creator in creators:
        creator.expected_reach = round(creator.median_views_last_10 * 0.85) if creator.median_views_last_10 else None
        estimated_cost = estimate_youtube_cost(creator.median_views_last_10)
        creator.estimated_cost_low = round(estimated_cost * 0.8, 2) if estimated_cost is not None else None
        creator.estimated_cost_high = round(estimated_cost * 1.2, 2) if estimated_cost is not None else None

        topic_points = _topic_fit_points(campaign, creator)
        geo_points = _geo_fit_points(campaign, creator)
        engagement_points = _engagement_points(creator, max_ratio)
        budget_points = _budget_points(campaign, creator)
        contact_points = 10 if creator.contact_method and creator.contact_method != "none" else 0
        safety_points = _brand_safety_points(creator)
        total = topic_points + geo_points + engagement_points + budget_points + contact_points + safety_points

        if creator.followers is None and creator.median_views_last_10 is None:
            total = min(total, 45)
            creator.notes = _append_note(creator.notes, "confidence=low")
        elif creator.followers is None or creator.median_views_last_10 is None:
            creator.notes = _append_note(creator.notes, "confidence=medium")

        creator.fit_score = round(total, 2)

    return sorted(creators, key=lambda creator: creator.fit_score or 0, reverse=True)


def estimate_youtube_cost(median_views_last_10: Optional[int]) -> Optional[float]:
    if median_views_last_10 is None:
        return None
    return max(150.0, (median_views_last_10 / 1000.0) * 25.0 * 1.15)


def _topic_fit_points(campaign: Campaign, creator: Creator) -> float:
    campaign_terms = _keyword_set([campaign.vertical, campaign.audience, campaign.brief_text])
    creator_terms = _keyword_set(creator.topic_tags_json or [])
    if not campaign_terms or not creator_terms:
        return 0.0
    overlap = len(campaign_terms & creator_terms)
    return min(30.0, overlap * 7.5)


def _geo_fit_points(campaign: Campaign, creator: Creator) -> float:
    if not creator.region_guess or creator.region_guess == "unknown":
        return 10.0
    campaign_geo = campaign.geo.strip().lower()
    creator_geo = creator.region_guess.strip().lower()
    if campaign_geo in creator_geo or creator_geo in campaign_geo:
        return 20.0
    return 0.0


def _engagement_points(creator: Creator, max_ratio: float) -> float:
    if not creator.followers or not creator.median_views_last_10 or max_ratio <= 0:
        return 0.0
    ratio = creator.median_views_last_10 / max(creator.followers, 1)
    return min(15.0, (ratio / max_ratio) * 15.0)


def _budget_points(campaign: Campaign, creator: Creator) -> float:
    if creator.estimated_cost_low is None:
        return 0.0
    return 15.0 if creator.estimated_cost_low <= campaign.budget_max else 0.0


def _brand_safety_points(creator: Creator) -> float:
    if creator.brand_safety_status == "unchecked":
        return 5.0
    if creator.content_safety_score is None:
        return 0.0
    return max(0.0, min(10.0, creator.content_safety_score / 10.0))


def _keyword_set(values: Iterable[str]) -> Set[str]:
    tokens: Set[str] = set()
    for value in values:
        for token in value.lower().replace("/", " ").replace("-", " ").split():
            clean = token.strip(",.!?()[]{}:;'\"")
            if len(clean) >= 3:
                tokens.add(clean)
    return tokens


def _append_note(notes: Optional[str], text: str) -> str:
    if not notes:
        return text
    return f"{notes}; {text}"
