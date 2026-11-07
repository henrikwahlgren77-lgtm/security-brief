# Architecture

## Scope

A small, self-hosted publication service for daily open-source security briefs. It stores dated reports, renders Markdown safely, provides an archive, and exposes a token-protected ingestion API.

## Components

- FastAPI application: HTML pages and ingestion API.
- SQLite: one durable database volume; one report per date.
- Jinja templates: server-rendered pages.
- Nginx on the host: TLS termination and reverse proxy.
- Docker Compose: isolated application service.

## Data flow

1. A trusted report-generation workflow creates a dated Markdown draft.
2. It sends JSON to POST /api/reports with an Authorization: Bearer token.
3. The app validates the payload and stores it as a draft. Re-submitting a date also returns it to draft, even if it was previously published.
4. An authenticated editor reviews the draft at /admin, validates source links and wording, and publishes it explicitly.
5. The public home page, archive, and report routes query published reports only; drafts return 404 on public detail routes.

## Security boundaries

- Repository contains code and documentation only, never production secrets or reports.
- Published reports are publicly readable. Drafts and editorial controls require HTTP Basic authentication.
- Publishing is a POST action with an authenticated, report-specific anti-CSRF token.
- Existing reports gain a `draft` status during schema migration, requiring editorial review before becoming public.
- Import API requires a high-entropy token.
- HTML output is sanitized; raw HTML from reports is not trusted.
- SQLite lives in a Docker named volume.
- App container is read-only except for temporary files and the mounted data volume.
- Nginx should enforce HTTPS before exposing the service publicly.
- Do not expose port 8000 directly to the internet.

## Editorial publication policy

- Draft is the default state; ingestion cannot publish.
- Only an authenticated editor can publish through `/admin`.
- Publication review checks that sources are linked, verified facts are distinguished from government assessments and editorial analysis, uncertainty is clear, and the report contains no sensitive operational details or unnecessary personal data.
- The public product is an open, source-based security publication. Source code and secrets remain separate; no email delivery, automated research, VPS changes, or DNS changes are part of this implementation.
- Status, publication timestamp, and publishing editor are retained in SQLite. A new ingestion for an already published date removes it from public pages until reviewed again.

## Integration boundary

The ChatGPT scheduled brief does not automatically call this API or send Gmail by itself. A separate, explicitly configured publisher is required. Do not add credentials to GitHub Actions or this repository until the deployment and secret-handling design is reviewed.
