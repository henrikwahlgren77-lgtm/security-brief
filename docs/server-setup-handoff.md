# Server setup handoff

This document is for the model or operator completing the VPS setup. It describes the current repository state, deployment sequence, checks, and limits. Do not treat it as evidence that the server has already been inspected or changed.

## Current state

- The user reports that `brief.kinetic.nu` is ready.
- The application code and editorial workflow are on `main` at `1e910cf`; local tests and the latest GitHub Actions run passed.
- Public pages show published reports only. `POST /api/reports` stores or updates a report as a draft. An authenticated editor publishes it at `/admin`.
- SQLite is stored in the Compose named volume `brief_data`. Existing rows migrate to `draft` and need editorial review before publication.
- Compose publishes the app only on host loopback at `127.0.0.1:8098`; the container listens on port 8000 internally.
- The server has not been inspected or changed by this work. No production `.env` or secrets are in the repository.
- DNS did not resolve from the previous agent environment, so verify the record from the VPS or another network before routing traffic.
- No scheduled report generation, Gmail delivery, or OAuth setup is included.

## Deployment objective

Deploy the app as an isolated service on the existing Debian VPS, then publish it at `https://brief.kinetic.nu` through the host's existing reverse proxy. Preserve all existing services, including TeamSpeak and any current websites. The source repository is public; never put credentials, reports, database files, or server configuration secrets in GitHub.

## 1. Inspect before changing anything

Start with a read-only inventory. Confirm the host identity and Debian version, free disk and memory, running services and containers, listening ports, Docker/Compose versions, firewall rules, reverse proxy, backups, and existing certificate management. Avoid printing environment files, private keys, tokens, or full proxy configurations that might contain secrets.

Confirm that DNS for `brief.kinetic.nu` resolves to this VPS and that the existing reverse proxy can route the hostname. Check that `127.0.0.1:8098` is unused. Do not assume it is available just because it is the app's configured port.

Record a short baseline before making changes. If a required port is occupied, identify its owner and choose a reviewed alternative; do not stop or reconfigure the existing service to free it without explicit approval.

## 2. Prepare the isolated checkout and secrets

Use `/opt/security-brief` if it does not already exist. If it exists, inspect it and preserve its data; do not overwrite it. Clone or update the public repository's `main` branch, then check the current commit and its Actions result.

Create `.env` from `.env.example` on the VPS. Set unique values for `APP_ADMIN_USER`, `APP_ADMIN_PASSWORD`, and `INGEST_TOKEN`. Generate strong secrets on the server, restrict `.env` to the service operator (for example, mode `600`), and never paste secret values into chat, shell history, logs, GitHub, or a ticket. Compose sets `DATABASE_PATH=/data/security_brief.sqlite3` and stores that file in the named volume.

Do not set or restore `PUBLIC_REPORTS`; public access is controlled by the report's database status. Never migrate old reports straight to `published`.

## 3. Start only this Compose project

From the checkout directory, validate the Compose configuration without displaying expanded secrets, then build and start this project's service:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

The expected service/container name is `security-brief`. Keep its host binding on `127.0.0.1:8098`; do not publish ports 8000 or 8098 to the public network. Do not run global Docker cleanup commands.

## 4. Add the site to the existing reverse proxy

Add a separate virtual host for `brief.kinetic.nu` to the existing proxy configuration. Preserve its other sites and services. Proxy HTTPS requests to `http://127.0.0.1:8098`, retain the original host, and forward the standard client IP and HTTPS scheme headers. Validate the specific configuration and reload the proxy using its normal safe procedure.

Use the existing certificate workflow if available. Request or renew a certificate only after DNS points at this VPS and the proxy can answer the validation challenge. Confirm the public site uses HTTPS before entering editor credentials; HTTP Basic credentials must not travel over plain HTTP.

Do not replace the proxy, alter unrelated virtual hosts, open new firewall ports, or change DNS as part of this handoff unless the user separately authorizes that specific change.

## 5. Verify without publishing test content

- `curl -fsS http://127.0.0.1:8098/health` returns `{"status":"ok"}`.
- `https://brief.kinetic.nu/` is publicly reachable over a valid HTTPS certificate.
- `/admin` requires HTTP Basic authentication; the configured editor can open the editorial queue in a browser.
- A request to `POST /api/reports` with an invalid bearer token is rejected. Do not submit a synthetic report to the production database for smoke testing.
- Public report pages do not reveal draft content. No report is public until an editor explicitly publishes it.
- Restart the `security-brief` Compose project and confirm the service returns healthy and the SQLite volume remains attached.
- Confirm the host still runs its pre-existing services and that no public listener was added for 8000 or 8098.

Do not copy tokens or passwords into commands, screenshots, terminal transcripts, or the final status report. Do not publish the first real report as part of deployment verification.

## 6. Rollback and completion report

If this service fails, stop only the `security-brief` Compose service and leave its named volume intact. Revert only the new `brief.kinetic.nu` proxy configuration if that change caused a problem. Never run `docker compose down -v`, `docker system prune`, broad firewall resets, or commands that remove shared volumes.

Report the inspected baseline, exact files/configuration changed, commit deployed, proxy/TLS result, health and authentication checks, backup status, and any remaining risks. Do not include secret values. Keep the report-generation schedule, automated publishing, Gmail, and OAuth out of scope.