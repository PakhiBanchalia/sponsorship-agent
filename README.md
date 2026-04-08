# Creator Sponsorship Agent

Single-user TinyFish accelerator MVP for scouting YouTube fitness creators, running brand safety checks, finding contact surfaces, and executing up to two outreach actions.

## Stack

- FastAPI
- Jinja2 + HTMX + Tailwind CDN
- SQLAlchemy + SQLite
- TinyFish Web Agent REST API
- Gmail SMTP

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in `TINYFISH_API_KEY`.
3. Fill in `BRAND_SAFETY_LLM_API_KEY`.
4. set `BRAND_SAFETY_LLM_PROVIDER` to `gemini` or `anthropic`. Default is `gemini`.
5. Fill in Gmail SMTP credentials if you want real email sends.
5. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

## Notes

- TinyFish prompt validation was performed before scaffolding using real discovery and enrichment runs.
- All TinyFish calls use async + poll.
- `streaming_url` is shown as an external link, not embedded.
- YouTube only for this MVP.
- Discovery timeout defaults to 300 seconds because live TinyFish search runs often exceed 120 seconds.
- Brand safety uses Gemini by default and can be switched to Anthropic via `BRAND_SAFETY_LLM_PROVIDER`.
