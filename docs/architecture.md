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

## Model-independent analysis

`app.analysis` defines a provider-neutral `complete(request)` contract and a four-role workflow:

1. `collection`: organize and summarize supplied source material.
2. `main_analysis`: develop the primary assessment.
3. `counter_analysis`: independently challenge the assessment.
4. `final_report`: prepare an editorial draft using both analyses.

The provider adapter is selected separately for each role by environment variables. For example:

```dotenv
ANALYSIS_COLLECTION_PROVIDER=mock
ANALYSIS_COLLECTION_MODEL=
ANALYSIS_MAIN_ANALYSIS_PROVIDER=mock
ANALYSIS_MAIN_ANALYSIS_MODEL=
ANALYSIS_COUNTER_ANALYSIS_PROVIDER=mock
ANALYSIS_COUNTER_ANALYSIS_MODEL=
ANALYSIS_FINAL_REPORT_PROVIDER=mock
ANALYSIS_FINAL_REPORT_MODEL=
```

Provider identifiers are adapter registration keys and model identifiers are opaque values passed through to that adapter. This repository does not assume or bundle a vendor's model names, SDK, endpoint, request format, or response format. To add OpenAI, Anthropic, or Mistral, implement `AnalysisProvider.complete`, translate `ProviderRequest`/`ProviderResponse` at that boundary, register the adapter under a configured provider key, and read its API key from a process environment variable. Never serialize credentials into `AnalysisConfig` or requests, check them into Git, or log them. The mock adapter demonstrates the contract without network access or credentials.

The pipeline always returns `EditorialStatus.DRAFT`. The independent counter-analysis is included in the final-report context and recorded separately; it has no approval or publication operation. `approve_for_publication` requires an explicit non-empty editor identity and returns a distinct approval record. The pipeline is not connected to `/api/reports`; only a separately controlled publisher may submit editor-approved text through that trusted endpoint.

Failures to select an adapter, call it, or receive a non-empty response stop the pipeline with `ProviderError`; no partial result is returned as a publishable report. Adapter exceptions are wrapped without exposing vendor exception text.

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

## Source collection

`app.analysis.fetcher.SourceFetcher` retrieves pages itself from hosts listed in `config/approved_sources.toml`; a model never supplies content or URLs that are trusted on its own. Each fetch attempt, including failures, is stored in the `sources` table (`app.analysis.sources.SourceStore`) with name, title, URL, canonical URL, stated publication time, event date, country, region, topic, origin type and original URL, fetch time, status, content hash and extracted text.

- Only `https` on the standard port is fetched. The host and every redirect target must be approved, and hosts resolving to non-public addresses are refused. Size, time and redirect count are limited.
- Publication time is recorded only when the page states it. Event date is set only by a caller that has established it. The fetch time is always recorded. The three are never merged.
- `independent_origins` groups republications and identical content under one origin so they count as one independent source.
- `find_unfetched_urls` lists URLs in text that have no successful recorded fetch; analysis output that cites any such URL must be rejected.
- The fetcher is meant to run in a separate scheduled job, not in the web container. The approved list is an initial proposal for editorial review. Media outlets are not yet listed.
- Known limit: DNS is resolved once for the address check and again by the HTTP client (see the `fetcher` module docstring).
