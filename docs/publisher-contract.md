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
  "report_date": "2026-11-08"
}
```

## Behavior

- `report_date` is an ISO date.
- `title` is limited to 200 characters.
- `markdown` is limited to 200,000 characters.
- A second submission for the same date updates that day's report.
- HTML is generated and sanitized when rendered; raw HTML from the report is not trusted.
- Missing or incorrect tokens are rejected.

## Publisher requirements

- Never run in the public browser.
- Keep the token in a secret store or protected environment file.
- Do not log the Authorization header.
- Retry transient network errors with bounded backoff.
- Treat HTTP 401/503 and validation errors as failures requiring attention.
- Do not publish until source validation and any required approval are complete.
