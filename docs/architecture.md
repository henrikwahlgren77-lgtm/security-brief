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

1. A trusted report-generation workflow creates a dated Markdown brief.
2. It sends JSON to POST /api/reports with an Authorization: Bearer token.
3. The app validates the payload, sanitizes rendered HTML, and upserts the report by date.
4. Readers open the latest report or archive on the website.
5. A separate mail-delivery step may send the same approved content via Gmail.

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
- Site access is private by default (PUBLIC_REPORTS=false).
- Import API requires a high-entropy token.
- HTML output is sanitized; raw HTML from reports is not trusted.
- SQLite lives in a Docker named volume.
- App container is read-only except for temporary files and the mounted data volume.
- Nginx should enforce HTTPS before exposing the service publicly.
- Do not expose port 8000 directly to the internet.

## Integration boundary

The ChatGPT scheduled brief does not automatically call this API or send Gmail by itself. A separate, explicitly configured publisher is required. Do not add credentials to GitHub Actions or this repository until the deployment and secret-handling design is reviewed.
