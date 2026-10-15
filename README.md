# CPA — Cognitive Persistence

Provider layer + service + API + two UIs. React (`web/`) is the primary UI; Streamlit is secondary.

## Setup
1. `uv sync`
2. Copy `.env.example` to `.env` and add a key for the provider you use.
3. Settings are read once at startup, so restart after editing `.env`.

## Run
- API: `uv run cpa-api`
- React UI: `cd web && npm install && npm run dev`
- Streamlit UI: `uv run cpa-ui`

## Checks
uv run pytest · uv run ruff check · uv run ruff format --check
cd web && npm run build && npm test
