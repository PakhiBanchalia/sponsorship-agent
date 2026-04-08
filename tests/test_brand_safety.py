import asyncio

from app.services import brand_safety


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return FakeResponse(self.payload)


def test_brand_safety_flags(monkeypatch):
    monkeypatch.setattr(
        brand_safety,
        "httpx",
        type("FakeHttpx", (), {"AsyncClient": lambda *args, **kwargs: FakeAsyncClient({"content": [{"text": '{"content_safety_score": 40, "red_flags": ["steroid content"], "summary": "risk"}'}]})}),
    )
    monkeypatch.setattr(
        brand_safety,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "brand_safety_llm_api_key": "key",
                "brand_safety_llm_provider": "anthropic",
                "brand_safety_llm_model": "model",
                "brand_safety_score_threshold": 60,
            },
        )(),
    )
    result = asyncio.run(brand_safety.run_brand_safety_check("brief", "fitness", "audience", [{"title": "x", "description_snippet": "y"}]))
    assert result["brand_safety_status"] == "flagged"
    assert result["red_flags"] == ["steroid content"]


def test_brand_safety_unchecked_on_failure(monkeypatch):
    class BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(brand_safety, "httpx", type("FakeHttpx", (), {"AsyncClient": lambda *args, **kwargs: BrokenClient()}))
    monkeypatch.setattr(
        brand_safety,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "brand_safety_llm_api_key": "key",
                "brand_safety_llm_provider": "gemini",
                "brand_safety_llm_model": "model",
                "brand_safety_score_threshold": 60,
            },
        )(),
    )
    result = asyncio.run(brand_safety.run_brand_safety_check("brief", "fitness", "audience", []))
    assert result["brand_safety_status"] == "unchecked"
