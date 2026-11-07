# Publisher API contract

The web application accepts one report at a time. The publisher must be a trusted server-side process.

## Endpoint

`POST /api/reports`

Required header:

`Authorization: Bearer <INGEST_TOKEN>`

Content type: `application/json`

Example body:

```json
{
  "report_date": "2026-11-08",
  "title": "Säkerhetsbrief – 8 november 2026",
  "markdown": "## Sammanfattning\n\nRapporttext med källhänvisningar."
}
```

Successful response:

```json
{
  "ok": true,
  "report_date": "2026-11-08",
  "status": "draft"
}
```

## Behavior

- `report_date` is an ISO date.
- `title` is limited to 200 characters.
- `markdown` is limited to 200,000 characters.
- A new submission creates a `draft`.
- A second submission for the same date updates its content and resets it to `draft`, including when it was already published.
- HTML is generated and sanitized when rendered; raw HTML from the report is not trusted.
- Missing or incorrect tokens are rejected with HTTP 401; if `INGEST_TOKEN` is not configured on the server, the endpoint returns HTTP 503.
- The successful response always has `"status": "draft"`; ingestion cannot publish.

## Editorial publishing

Editors authenticate with `APP_ADMIN_USER` and `APP_ADMIN_PASSWORD` at `/admin`. The editorial queue supports explicit publishing through a CSRF-protected POST action. A report is public only after this action. The home page and report routes return published reports only; a draft's public detail URL returns 404.

## Publisher requirements

- Never run in the public browser.
- Keep the token in a secret store or protected environment file.
- Do not log the Authorization header.
- Retry transient network errors with bounded backoff.
- Treat HTTP 401/503 and validation errors as failures requiring attention.
- Check citations and source links, distinguish verified facts from official assessments and editorial analysis, describe uncertainty, and review for sensitive operational information and unnecessary personal data before publishing.
