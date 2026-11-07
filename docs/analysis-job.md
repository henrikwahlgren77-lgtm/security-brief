# Running the analysis job

The job turns a list of source URLs into a **draft** for editorial review. It does not find sources, publish, or send email. Read [architecture.md](architecture.md#analysis-job-and-editorial-review) for what it checks.

## One-time setup on the server

1. `cp .env.analysis.example .env.analysis && chmod 600 .env.analysis`
2. In `.env.analysis`: set `INGEST_TOKEN` to the same value as in `.env`; set a provider and **model name** for each role; add the API key for each vendor you use. Use a different vendor for the counter-analysis than for the main analysis.
3. `mkdir -p analysis-input` and put a `urls.txt` in it: one URL per line, `#` for comments. Only hosts in `config/approved_sources.toml` are fetched; anything else is recorded as blocked.
4. `docker compose build` (the image now includes `prompts/` and `config/`).

## Each run

```bash
docker compose run --rm analysis --date 2026-11-08 --urls-file /input/urls.txt
```

Use `--no-submit` to analyse and print the report without submitting anything. Exit code 0 means a draft was submitted; 1 means a check failed and nothing was submitted (the message says which; the run is still in the run log).

Then open `https://brief.kinetic.nu/admin`, choose **Granska underlag och publicera**, read the review page and publish.

## Inspect runs

```bash
docker compose run --rm --entrypoint python analysis -m app.analysis.runs list
docker compose run --rm --entrypoint python analysis -m app.analysis.runs show <run id>
docker compose run --rm --entrypoint python analysis -m app.analysis.runs usage
```

These print metadata only (status, models, prompt versions, tokens, cost), never source text or model output.

## Before relying on it

- The model adapters have not been run against the live vendor APIs from this repository. Do a first run with `--no-submit` and read the whole result.
- Models must return a valid JSON block in the main and counter stages. If a vendor fails this often, the run stops with an error naming the stage.
- Costs are shown only if you set both price variables for a role.
- Resubmitting a date resets a published report to draft.
