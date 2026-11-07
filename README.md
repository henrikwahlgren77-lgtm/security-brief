# Security Brief

A small, self-hosted publication service for the Northern Europe daily security brief.

## Principles

- Public source code; secrets and operational data stay on the VPS.
- Public-source intelligence only. No classified or access-controlled material.
- The site is public, but every incoming report is a private draft until an authenticated editor publishes it.
- No public registration, comments, uploads, or arbitrary HTML.
- Reports are submitted through a token-protected ingestion API and rendered with sanitized Markdown.

## Stack

- Python 3.12
- FastAPI + Jinja2
- SQLite
- Docker Compose
- Nginx reverse proxy + HTTPS (configured on the VPS)

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set APP_ADMIN_USER, APP_ADMIN_PASSWORD and INGEST_TOKEN before starting.
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the public site and http://127.0.0.1:8000/admin for the editorial queue. Configure a unique `APP_ADMIN_USER`, `APP_ADMIN_PASSWORD`, and `INGEST_TOKEN` before starting. Reports remain drafts until an authenticated editor publishes them.

## VPS deployment (after server inventory and DNS)

```bash
git clone https://github.com/henrikwahlgren77-lgtm/security-brief.git
cd security-brief
cp .env.example .env
# Edit .env and generate unique secrets before starting.
docker compose up -d --build
```

The app binds to host loopback at `127.0.0.1:8098`. Configure the existing host reverse proxy to forward HTTPS traffic for `brief.kinetic.nu` to that address. Do not expose port 8000 or 8098 publicly.

## Ingest a report

Send Markdown as JSON to `POST /api/reports`:

```bash
curl -X POST http://127.0.0.1:8098/api/reports \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"report_date":"2026-11-08","title":"Säkerhetsbrief – 8 november 2026","markdown":"## Sammanfattning\n\nRapporttext..."}'
```

Keep the ingestion token private. The endpoint is intended for a trusted publisher, not a browser. Ingestion never publishes a report; every submission and update sets its status to `draft`. An editor signs in at `/admin` and publishes only after checking source links, separating verifiable facts from official assessments and editorial analysis, and reviewing the report for sensitive operational detail.

## Configuration and data

See `.env.example`. Do not commit `.env`, database files, credentials, API tokens, OAuth refresh tokens, or generated reports. SQLite is stored in the Docker volume `brief_data`.

## Current scope

The application includes a public archive of published reports, sanitized Markdown rendering, security headers, token-protected draft ingestion, and authenticated editorial publishing. Automated research, Gmail delivery, the scheduled publisher, deployment, backups, and DNS are separate integration steps and are not active yet.
