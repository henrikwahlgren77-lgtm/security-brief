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

## Prompts and provider adapters

- Prompts live in `prompts/<role>/v<N>.md`, one directory per role (`collection`, `main_analysis`, `counter_analysis`, `final_report`). A run records the version and SHA-256 of each prompt it used. Never edit a used version in place; add the next one.
- Prompts instruct models to cite sources only as `[S#]`. `app.analysis.context.format_sources_for_prompt` assigns the ids and delimits page text as untrusted data; `expand_citations` turns ids back into links to URLs the collector actually fetched and reports unknown ids. `find_unfetched_urls` still catches any URL a model writes itself.
- `app.analysis.adapters` contains `OpenAIAdapter`, `AnthropicAdapter` and `MistralAdapter`, thin HTTPS clients over `httpx` that implement the neutral `complete()` contract. Choosing vendor and model per role is configuration only (see `.env.example`). Retries (3 attempts, `Retry-After` honoured and capped), timeouts, truncated/refused/empty answers and error text are handled in one shared base class. Error messages never contain request content, response bodies or keys.
- Keys are read from the process environment at call time. Only the analysis job needs them; the web container does not.
- Tokens and latency are returned per call. Cost is computed only from prices the operator configures; no vendor prices are built into the code.
- A counter-analysis on the same provider and model as the main analysis produces a warning on the draft.
- Why not the vendor SDKs or a gateway library: see `docs/provider-review.md`.

## Run log

`app.analysis.runs.RunStore` records every analysis run in two tables of the same SQLite database: `analysis_runs` (status, timing, configuration snapshot, which stored sources were used, source-material hash, the draft report, warnings) and `analysis_stages` (per stage: provider, requested and served model, prompt version and SHA-256, the exact input and output text, tokens, estimated cost, latency, attempts, finish reason, error). Pass `run_store=RunStore(path)` to `AnalysisPipeline` to enable it.

- Stages are written as they finish, so a run that fails halfway still shows exactly how far it got and why. The failing stage stores the input it was given.
- With the stored inputs, the source records they refer to (`sources.id`) and the prompt files (checked by SHA-256), a draft can be traced back and its stages re-run.
- A run can only hold a `draft` report. Finishing a run is one-way.
- Credentials are never stored: the configuration snapshot has provider, model, token limit, prompt version and operator-supplied prices only. Error text recorded for unexpected exceptions is reduced to the exception type; adapter errors are already free of request content.
- Cost is a sum over stages whose prices are configured. `RunRecord.cost_complete` says whether every stage had one; otherwise `known_cost` is a lower bound.
- Log lines (logger `app.analysis.runs`) carry run id, role, provider, model, prompt version, tokens, latency, attempts and errors, never source text or model output.
- `python -m app.analysis.runs list|show <id>|usage` prints metadata only.
- Stored text is as sensitive as the sources. Keep the database on the analysis host and back it up with the rest of the data (backups are not set up yet).

## Assessment layer

The main and counter stages (prompt version v2) return prose plus one machine-readable ```json block. `app.analysis.assessment` parses it strictly (pydantic, unknown fields rejected, ids pattern-checked, error text never echoes model output) and applies deterministic rules. A stage whose block is invalid gets one corrective retry; a second failure stops the run.

Rules applied in code, after the main analysis and before the reviewer sees it:

- **Evidence.** `confirmed` needs a primary source (tier 1, original) or at least two independent origins. Republications of one original, and pages with identical content, count once. Otherwise the claim is downgraded to `credible_unconfirmed`. A factual claim with no provided source becomes `unconfirmed`. Own analysis is never `confirmed`. Source ids the system never provided are flagged and ignored.
- **Change.** Without an earlier report nothing can be `new` (it becomes `no_comparison`). A `new` claim whose sources were all used in the previous report, or whose wording nearly matches an earlier claim, becomes `continuing`. A `new` claim whose dated sources are all older than the reporting window (default 24 hours) becomes `background`. `changed`/`continuing`/`confirmation`/`denial` must reference an existing earlier claim (P#). Missing dates cannot prove newness and are reported as info.
- **Article 5.** A category other than `no_basis` without a provided source is reset to `no_basis`. `attack` without a tier 1 source is flagged. Percentages or other numeric risk figures in the article 5 reasoning, indicators or hypotheses are flagged (no documented method exists).
- **Disagreement.** Decisive and substantial objections from the counter-analysis must appear, by id, under "Osäkerheter och luckor". Any the writer omitted are appended by the system and recorded. Only a human editor can dismiss an objection.

The calibrated claim list, not the model's own labels, is what the reviewer and the writer receive, and it is stored with the run (`analysis_runs.assessment`). The next run is shown the last *completed* run's calibrated claims as P1, P2 ... so it can mark continuations, confirmations and denials; set `use_history=False` to disable this. Unfinished or failed runs are never used as history.

Issues are returned in `AnalysisDraft.warnings` for the editor; `info` items (for example "no earlier report", "no source states a date") stay in the run log only.

Limits: the rules check structure, support and consistency, not truth. They cannot tell whether a primary source is itself wrong or misleading, and the text-similarity repeat check is a heuristic. The editor still has to read the draft.

## Analysis job and editorial review

`python -m app.analysis.job` (Compose service `analysis`, profile `analysis`, started only on demand) fetches the given URLs from approved hosts, runs the pipeline and submits ONE draft through `POST /api/reports`, the same trusted path as any publisher. It never writes to the `reports` table and never publishes.

Hard checks before anything is submitted (any failure stops the job; the run stays in the run log):

- roles configured with the mock provider are refused (placeholder text must never become a draft);
- every `[S#]` citation must map to a source that was actually fetched, and is replaced by a link to its URL;
- no other URL may appear in the report (`find_unfetched_urls`);
- all eight required sections must be present, in order; the title is fixed by the job, not by a model;
- the API must confirm the report was stored as a `draft`. The token is sent only over https, or http to this deployment (`localhost`, `127.0.0.1`, `security-brief`).

Review in `/admin`: a draft with an `analysis_run_id` is not published straight from the queue. `/admin/reports/{date}/review` (editor login) shows warnings, unresolved objections (marked when the system had to add them), calibrated claims with downgrades, alternative interpretations, the system's notes, the sources with tier, dates and links, and the model steps with tokens and cost. All model and source text is HTML-escaped; links come only from stored http(s) URLs. The publish form requires a checkbox, and the server rejects publishing without it (`ack_review=yes`) in addition to the existing CSRF token and login. Reports without a run link behave as before.

Trust boundaries:

- The web app and the job share one SQLite file on the `brief_data` volume. The web app only reads analysis tables, through a read-only connection (`app.analysis.review`) that cannot create or change them. The job writes analysis tables and sources directly, and reports only through the API.
- Model API keys live only in `.env.analysis`, read by the job container. The web container never receives them. `INGEST_TOKEN` is in both files and must match.
- The run log and `sources` table contain full source text and model output. Anyone with the editor login can see the extracts the review page shows; keep the volume private.
- Two containers write the same SQLite file on one host; short transactions and SQLite's file locking handle this. Do not run several analysis jobs at once.

Not built: source discovery (the operator supplies URLs), scheduling, and any automatic publishing.

## Source discovery

`app.analysis.discovery` finds candidate pages from feeds and index pages configured in `config/feeds.toml` (shipped with no feeds). It reuses the guarded `SourceFetcher.fetch_document`, parses RSS/Atom with a strict expat-based parser that refuses any DOCTYPE or entity declaration, filters by approved host, age window, earlier use and keyword relevance, ranks, and records every decision in `discovery_runs`, `discovery_feeds` and `discovery_items`. It makes no model calls. The job runs it with `--discover`; the selected URLs then go through the normal fetch, analysis, validation and draft submission. Details, safety properties and the GitHub review: `docs/source-discovery.md`.
