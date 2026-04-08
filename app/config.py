from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


load_dotenv()


class Settings(BaseModel):
    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    sqlite_path: str = Field(default="data/app.db", alias="SQLITE_PATH")

    tinyfish_api_key: str = Field(default="", alias="TINYFISH_API_KEY")
    tinyfish_api_base: str = Field(default="https://agent.tinyfish.ai/v1", alias="TINYFISH_API_BASE")
    default_country_code: str = Field(default="US", alias="DEFAULT_COUNTRY_CODE")
    max_discovery_runs: int = Field(default=2, alias="MAX_DISCOVERY_RUNS")
    max_enrichment_runs: int = Field(default=4, alias="MAX_ENRICHMENT_RUNS")
    max_brand_safety_runs: int = Field(default=1, alias="MAX_BRAND_SAFETY_RUNS")
    max_contact_runs: int = Field(default=2, alias="MAX_CONTACT_RUNS")
    max_outreach_runs: int = Field(default=1, alias="MAX_OUTREACH_RUNS")

    tinyfish_timeout_discovery: int = Field(default=300, alias="TINYFISH_TIMEOUT_DISCOVERY")
    tinyfish_timeout_enrichment: int = Field(default=180, alias="TINYFISH_TIMEOUT_ENRICHMENT")
    tinyfish_timeout_contact: int = Field(default=180, alias="TINYFISH_TIMEOUT_CONTACT")
    tinyfish_timeout_outreach: int = Field(default=240, alias="TINYFISH_TIMEOUT_OUTREACH")

    allow_email_send: bool = Field(default=True, alias="ALLOW_EMAIL_SEND")
    gmail_from_address: str = Field(default="", alias="GMAIL_FROM_ADDRESS")
    gmail_app_password: str = Field(default="", alias="GMAIL_APP_PASSWORD")

    brand_safety_llm_api_key: str = Field(default="", alias="BRAND_SAFETY_LLM_API_KEY")
    brand_safety_llm_provider: str = Field(default="gemini", alias="BRAND_SAFETY_LLM_PROVIDER")
    brand_safety_llm_model: str = Field(default="gemini-2.5-flash", alias="BRAND_SAFETY_LLM_MODEL")
    brand_safety_score_threshold: int = Field(default=60, alias="BRAND_SAFETY_SCORE_THRESHOLD")

    model_config = {"populate_by_name": True}

    @property
    def sqlite_url(self) -> str:
        path = Path(self.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    values = {}
    for field_name, field in Settings.model_fields.items():
        env_name = field.alias or field_name.upper()
        env_value = os.getenv(env_name)
        if env_value is not None:
            values[field_name] = env_value
    return Settings(**values)
