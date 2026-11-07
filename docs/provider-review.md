# Provider integration review (GitHub First)

Date: 2026-11-07. Scope: how to call OpenAI, Anthropic and Mistral from the analysis job.

## What was checked

Shallow clones (30 commits) of each repository, read as text only (no code from them was executed): latest commit date, licence file, declared runtime dependencies, size, number of distinct authors in the last 30 commits, and the request/response shapes in the source.

## What could NOT be checked

Stars, forks, open issues, GitHub security advisories and CVEs, dependency vulnerabilities, PyPI release history and test/CI quality. This session had git read access only, not the GitHub API. Security classification is therefore **Unknown** for every candidate, and no weighted score is given; a score would rest on missing data.

## Findings

| Candidate | Version / latest commit | Licence | Declared runtime deps | Python LOC (incl. generated) |
|---|---|---|---|---|
| openai/openai-python | 3.47.0 / 2026-11-07 | Apache-2.0 | 6 | ~387k |
| anthropics/anthropic-sdk-python | 2.6.0 / 2026-11-05 | Apache-2.0 (+ MIT file) | 8 (incl. opentelemetry-api) | ~311k |
| mistralai/client-python | 3.7.0 / 2026-11-03 | Apache-2.0 | 8 (incl. opentelemetry, dateutil) | ~208k |
| BerriAI/litellm | 1.110.0 / 2026-11-07 | Mixed ("portions licensed as follows"; not reviewed in detail) | 26 (incl. boto3, huggingface-hub, tokenizers, aiohttp) | ~3.8M |

- All four are actively maintained (commits within the last week; several authors).
- All three vendor SDKs depend on `httpx2`, a different package from the `httpx` this project already uses. Adopting the SDKs would add a second HTTP stack.
- The gateway library adds by far the largest dependency and code surface for something this project does with one POST request per vendor.
- The request/response shapes needed (system prompt + user message in; text, stop reason and token counts out) are small and identical in kind across vendors: `POST /v1/chat/completions` (OpenAI, Mistral) and `POST /v1/messages` (Anthropic).

## Decision

Option D for the adapter layer: write ~200 lines of thin adapters over the existing `httpx` dependency (`app/analysis/adapters.py`), behind the existing neutral `complete()` contract. Reasons: smallest dependency and attack surface, one shared and tested retry/error path, no leakage of vendor exception text, and a vendor switch stays a configuration change. Cost: we track API changes ourselves.

Risks and mitigations:

- API drift: adapters treat any unexpected response shape as an error and are covered by tests built from SDK-verified shapes. They have **not** been run against the live APIs from this repository; do that once with real keys before relying on them.
- The Anthropic API version header default was copied from the SDK source and is overridable with `ANTHROPIC_API_VERSION`.
- Revisit if more vendors, streaming, tool use or structured-output features are needed; the official SDKs are then the natural next step. Re-check advisories at that time.
