# Security Brief

A small, self-hosted publication service for the Northern Europe daily security brief.

## Principles

- Public source code; secrets and operational data stay on the VPS.
- Public-source intelligence only. No classified or access-controlled material.
- Reports are private by default. Set `PUBLIC_REPORTS=true` only when public publication is intentional.
- No public registration, comments, uploads, or arbitrary HTML.
- Reports are submitted through a token-protected ingestion API.
- AI analysis output is a draft; a human editor must approve it before it enters the publishing workflow.

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

Open http://127.0.0.1:8000. The report archive is private unless `PUBLIC_REPORTS=true`.

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

Keep the ingestion token private. The endpoint is intended for a trusted publisher, not a browser.

## Configuration and data

See `.env.example`. Do not commit `.env`, database files, credentials, API tokens, OAuth refresh tokens, or generated reports. SQLite is stored in the Docker volume `brief_data`.

## Model-independent analysis

The analysis layer uses one provider contract and four separately configurable roles: collection and summary, main analysis, independent counter-analysis, and final report. Each role accepts an `ANALYSIS_<ROLE>_PROVIDER` and optional `ANALYSIS_<ROLE>_MODEL` environment variable. The default `mock` provider runs without network access or API keys. See [docs/architecture.md](docs/architecture.md) for configuration examples and the adapter contract.

No vendor API adapter is enabled yet. OpenAI, Anthropic, and Mistral can be added behind the shared contract without changing the workflow; until then, provider selection for those names fails clearly. API credentials must be supplied to a future adapter through process environment variables only. Never place keys in code, config files, prompts, logs, or GitHub.

Every workflow result is marked `draft`. Counter-analysis is advisory and cannot accept, reject, or publish the main analysis. Publication approval is a separate explicit editor action. The existing report ingestion endpoint remains a trusted publisher interface and is not called by the AI workflow.

## Current scope

The application foundation includes report storage, archive, sanitized Markdown rendering, access control, security headers, and a token-protected ingestion API. The analysis workflow currently includes only its shared abstraction and mock provider. Concrete provider adapters, source collection, Gmail delivery, the scheduled publisher, deployment, backups, and DNS are separate integration steps and are not active yet.
