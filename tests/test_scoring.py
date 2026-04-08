from app.models import Campaign, Creator
from app.services.scoring import estimate_youtube_cost, score_creators


def make_campaign() -> Campaign:
    return Campaign(
        brand_name="Volt",
        brief_text="Need clean fitness creators",
        budget_min=3000,
        budget_max=10000,
        geo="US",
        audience="women 18-34 fitness",
        vertical="fitness wellness",
        status="draft",
    )


def test_estimate_youtube_cost():
    assert estimate_youtube_cost(100000) == max(150.0, (100000 / 1000.0) * 25.0 * 1.15)


def test_score_creators_caps_when_both_key_metrics_missing():
    campaign = make_campaign()
    creator = Creator(
        campaign_id=1,
        platform="youtube",
        name="Creator",
        profile_url="https://youtube.com/@creator",
        followers=None,
        median_views_last_10=None,
        region_guess="US",
        topic_tags_json=["fitness", "wellness"],
        recent_video_samples_json=[],
        brand_safety_status="unchecked",
        red_flags_json=[],
        status="enriched",
    )
    ranked = score_creators(campaign, [creator])
    assert ranked[0].fit_score <= 45


def test_score_creators_awards_default_brand_safety_points():
    campaign = make_campaign()
    creator = Creator(
        campaign_id=1,
        platform="youtube",
        name="Creator",
        profile_url="https://youtube.com/@creator",
        followers=100000,
        median_views_last_10=50000,
        region_guess="US",
        topic_tags_json=["fitness", "wellness"],
        recent_video_samples_json=[],
        contact_method="email",
        brand_safety_status="unchecked",
        red_flags_json=[],
        status="enriched",
    )
    ranked = score_creators(campaign, [creator])
    assert ranked[0].fit_score is not None
    assert ranked[0].fit_score >= 50
