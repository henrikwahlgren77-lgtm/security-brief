# Security Brief

A small, self-hosted publication service for the Northern Europe daily security brief.

## Principles

- Public source code; secrets and operational data stay on the VPS.
- Public-source intelligence only. No classified or access-controlled material.
- The site is public, but every incoming report is a private draft until an authenticated editor publishes it.
- No public registration, comments, uploads, or arbitrary HTML.
- Reports are submitted through a token-protected ingestion API and rendered with sanitized Markdown.
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
pip install -r requirements-dev.txt
cp .env.example .env
# Set APP_ADMIN_USER, APP_ADMIN_PASSWORD and INGEST_TOKEN before starting.
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the public site and http://127.0.0.1:8000/admin for the editorial queue. Configure a unique `APP_ADMIN_USER`, `APP_ADMIN_PASSWORD`, and `INGEST_TOKEN` before starting. Reports remain drafts until an authenticated editor publishes them.

## VPS deployment

```bash
git clone https://github.com/henrikwahlgren77-lgtm/security-brief.git
cd security-brief
cp .env.example .env
# Edit .env and generate unique secrets before starting.
docker compose up -d --build
```

The app binds to host loopback at `127.0.0.1:8098`. Configure the existing host reverse proxy to forward HTTPS traffic for `brief.kinetic.nu` to that address. Do not expose port 8000 or 8098 publicly.

For the server setup handoff, inventory sequence, deployment checks, and rollback boundaries, see [docs/server-setup-handoff.md](docs/server-setup-handoff.md). The service is deployed on the VPS behind Caddy (see that document for the checks and rollback boundaries).

## Ingest a report

Send Markdown as JSON to `POST /api/reports`:

```bash
curl -X POST http://127.0.0.1:8098/api/reports \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"report_date":"2026-11-08","title":"Säkerhetsbrief – 8 november 2026","markdown":"## Sammanfattning\n\nRapporttext..."}'
```

Keep the ingestion token private. The endpoint is intended for a trusted publisher, not a browser. Ingestion never publishes a report; every submission and update sets its status to `draft`. An editor signs in at `/admin` and publishes only after checking source links, separating verifiable facts from official assessments and editorial analysis, and reviewing the report for sensitive operational detail.

## Design

The visual identity, typography (self-hosted Newsreader and IBM Plex under the SIL OFL) and component rules are documented in [docs/design-system.md](docs/design-system.md), with the research behind them in [docs/design-research.md](docs/design-research.md) and licences in [docs/fonts.md](docs/fonts.md). To look at the design with fictional sample content, run locally with `DESIGN_PREVIEW=1`; it is off by default and must never be enabled on the public site.

## Configuration and data

See `.env.example`. Do not commit `.env`, database files, credentials, API tokens, OAuth refresh tokens, or generated reports. SQLite is stored in the Docker volume `brief_data`.

## Model-independent analysis

The analysis layer uses one provider contract and four separately configurable roles: collection and summary, main analysis, independent counter-analysis, and final report. Each role accepts an `ANALYSIS_<ROLE>_PROVIDER` and optional `ANALYSIS_<ROLE>_MODEL` environment variable. The default `mock` provider runs without network access or API keys. See [docs/architecture.md](docs/architecture.md) for configuration examples and the adapter contract.

No vendor API adapter is enabled yet. OpenAI, Anthropic, and Mistral can be added behind the shared contract without changing the workflow; until then, provider selection for those names fails clearly. API credentials must be supplied to a future adapter through process environment variables only. Never place keys in code, config files, prompts, logs, or GitHub.

Every workflow result is marked `draft`. Counter-analysis is advisory and cannot accept, reject, or publish the main analysis. Publication approval is a separate explicit editor action. The existing report ingestion endpoint remains a trusted publisher interface and is not called by the AI workflow.

## Current scope

The application includes a public archive of published reports, sanitized Markdown rendering, security headers, token-protected draft ingestion, and authenticated editorial publishing. It is deployed at `https://brief.kinetic.nu` behind the host's Caddy reverse proxy. An analysis job (`docs/analysis-job.md`) can fetch approved sources, run a four-stage multi-model analysis with an independent review, and submit a draft with its evidence for editorial review at `/admin`. It is run on demand, is not scheduled, finds no sources by itself, and its model adapters have not been run against the live APIs. Gmail delivery, scheduling, and backups are not active yet.
