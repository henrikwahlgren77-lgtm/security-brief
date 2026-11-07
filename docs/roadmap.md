# Build plan

## Phase 1 — Publication foundation (current)

- FastAPI application and responsive report/archive pages.
- SQLite persistence in a Docker named volume.
- Markdown rendering with HTML sanitization.
- Public pages for published reports and an authenticated editorial queue.
- Draft-by-default report ingestion through a token-protected endpoint.
- Container runs as a non-root user, read-only root filesystem, and binds only to host loopback.

## Phase 2 — VPS deployment

- Read-only inventory of existing services, ports, reverse proxy, firewall, and backup jobs.
- Confirm free host port and current reverse-proxy arrangement.
- Deploy as an isolated Compose project; do not replace or modify existing services.
- Add Nginx virtual host for brief.kinetic.nu and issue HTTPS certificate after DNS is configured.
- Verify HTTPS, authentication, API token rejection, persistence, and restart behavior.

## Phase 3 — Report generation and publishing

- Implement a separate scheduled publisher; do not assume the ChatGPT scheduled task can call this VPS endpoint.
- Use public sources only, with source URLs, timestamps, and explicit separation of fact, agency assessment, media reporting, and analysis.
- Validate the report structure before publication.
- Require explicit human editorial approval before each report is publicly released.
- Never let ingestion publish automatically.

## Phase 4 — Gmail delivery

- Use the Gmail API with the minimum send-only OAuth scope.
- Store OAuth client/refresh credentials only on the VPS, outside the repository.
- Send to a configurable recipient; never hardcode a personal email address.
- Log delivery status without storing OAuth tokens or full sensitive credentials.

## Phase 5 — Operations

- Back up the SQLite volume and test restore.
- Add health monitoring and failure notifications.
- Add rate limits at the reverse proxy and API request-size limits.
- Document token rotation, restore, and update procedures.

## Publication decision

The source repository and published reports are public. Each new report is stored as a private draft and requires authenticated editorial review before it appears on the public site. The review must verify source links, separate facts from official assessments and editorial analysis, and screen for sensitive operational details.
