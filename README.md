# Security Brief

A small self-hosted publication service for the Northern Europe daily security brief.

## Principles

- Public source code; secrets and operational data stay on the VPS.
- Public-source intelligence only. No classified or access-controlled material.
- Reports are private by default. Set `PUBLIC_REPORTS=true` only when public publication is intentional.
- No public registration, comments, uploads, or arbitrary HTML.
- Reports are submitted through a token-protected ingestion API.

## Stack

- Python 3.12+
- FastAPI + Jinja2
- SQLite
- Nginx reverse proxy + HTTPS (deployment guide to follow)

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set APP_ADMIN_USER, APP_ADMIN_PASSWORD and INGEST_TOKEN before starting.
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The report archive is private unless `PUBLIC_REPORTS=true`.

## Ingest a report

Send Markdown as JSON to `POST /api/reports`:

```bash
curl -X POST http://127.0.0.1:8000/api/reports \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"report_date":"2026-11-08","title":"Säkerhetsbrief – 8 november 2026","markdown":"## Sammanfattning\n\nRapporttext..."}'
```

The endpoint is intended for a trusted automation running outside the public browser. Keep the token private.

## Configuration

See `.env.example`. Do not commit `.env`, database files, credentials, API tokens, OAuth refresh tokens, or generated reports.

## Current scope

This is the first application foundation: report storage, archive, rendering, access control, and ingestion API. Automated research, Gmail delivery, scheduling, deployment, backups, and DNS are separate integration steps and are not active yet.
