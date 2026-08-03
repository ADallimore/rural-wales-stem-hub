# AGENTS.md

## Purpose
This repository contains the Rural Wales STEM Pathways Hub static website and a scheduled Python scraper that discovers opportunities and writes them to Google Sheets.

## Repository Layout
- `index.html`, `about.html`, `contact.html`, `guides.html`, `opportunities.html`, `resources.html`, `suggest.html`, `timeline.html`: static site pages
- `scraper.py`: scheduled opportunity discovery and enrichment pipeline
- `.github/workflows/scraper.yml`: daily GitHub Actions workflow for running `scraper.py`
- `README.md`: brief repository description

## Tech Stack
- Static HTML site (no frontend build system)
- Python 3.11 scraper
- Runtime dependencies used by workflow: `gspread`, `requests`, `duckduckgo_search`

## Working Guidelines for Agents
1. Keep changes minimal and scoped to the request.
2. Preserve existing page structure and content tone when editing HTML.
3. Avoid introducing new dependencies unless required.
4. Do not commit secrets. `credentials.json` is generated in CI from a secret and should not be committed.
5. Prefer configuration through environment variables for integrations (e.g., `DISCORD_WEBHOOK_URL`).

## Validation
- For static content changes: verify file integrity and links manually.
- For Python changes: run syntax validation locally:
  - `python -m py_compile scraper.py`
- If scraper logic is changed, ensure it remains compatible with the workflow in `.github/workflows/scraper.yml`.

## CI Notes
- Scheduled workflow: **Daily STEM Opportunity Scraper** (`.github/workflows/scraper.yml`)
- Trigger types: cron + manual dispatch
- CI recreates `credentials.json` from `CREDENTIALS_JSON` secret before execution.
