"""Daily analysis job: fetch approved sources, analyse, submit a DRAFT.

    python -m app.analysis.job --date 2026-11-08 --urls-file urls.txt

The job never publishes. It submits through the same token-protected
``POST /api/reports`` as any other publisher, which always stores a draft, and
links the draft to its run log so the editor can review the evidence at
``/admin`` before publishing.

What it does NOT do: find the URLs. Source discovery (feeds, index pages) is a
separate step; today the operator supplies the URLs to analyse. Only hosts on
the approved list (``config/approved_sources.toml``) are ever fetched.

Before anything is submitted the report must pass hard checks: every ``[S#]``
citation maps to a fetched source, no URL appears that was not fetched, and
all required sections are present. A report that fails is not submitted.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from .adapters import build_registry
from .assessment import AssessmentError
from .context import expand_citations, format_sources_for_prompt
from .fetcher import SourceFetcher
from .pipeline import AnalysisPipeline
from .prompts import PromptError
from .providers import AnalysisConfig, ProviderError, ProviderRegistry
from .runs import RunStore
from .sources import FetchStatus, SourceError, SourceStore, canonicalize_url, find_unfetched_urls, validate_url

logger = logging.getLogger("app.analysis.job")

MAX_URLS = 30
MAX_MARKDOWN_CHARS = 200_000
REQUIRED_HEADINGS = [
    "## Sammanfattning",
    "## Verifierade fakta",
    "## Myndigheters och regeringars bedömningar",
    "## Mediarapportering",
    "## Säkerhetsläget i Norden och Östersjöregionen",
    "## Redaktionell analys",
    "## Osäkerheter och luckor",
    "## Källor",
]
_MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
           "oktober", "november", "december"]
_INTERNAL_HOSTS = {"localhost", "127.0.0.1", "::1", "security-brief"}


class JobError(RuntimeError):
    """The job stopped before submitting; the message contains no secrets or model output."""


@dataclass(frozen=True, slots=True)
class JobResult:
    run_id: str
    report_date: date
    submitted: bool
    markdown: str
    warnings: tuple[str, ...]
    sources_ok: int
    sources_failed: int


def swedish_title(report_date: date) -> str:
    return f"Säkerhetsbrief – {report_date.day} {_MONTHS[report_date.month - 1]} {report_date.year}"


def read_urls(path: Path | str) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def check_api_url(url: str) -> str:
    """The publication API must be reached over https, or plain http only inside the deployment."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise JobError("the publication API URL must be an http(s) URL without credentials")
    if parts.scheme == "http" and parts.hostname not in _INTERNAL_HOSTS:
        raise JobError("the publication API URL must use https unless it points at this deployment")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise JobError("the publication API URL must be a base URL without a path")
    return f"{parts.scheme}://{parts.netloc}"


def submit_via_api(
    base_url: str, token: str, payload: Mapping[str, object], *, transport: httpx.BaseTransport | None = None
) -> dict:
    """POST the draft. The token is sent as a header only and never logged or put in an error."""
    if not token:
        raise JobError("INGEST_TOKEN is not set")
    try:
        with httpx.Client(transport=transport, timeout=30.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(f"{base_url}/api/reports", json=payload, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise JobError(f"could not reach the publication API ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise JobError(f"the publication API answered HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise JobError("the publication API answered with something that is not JSON") from None
    if not isinstance(data, dict) or data.get("ok") is not True or data.get("status") != "draft":
        raise JobError("the publication API did not confirm that the report was stored as a draft")
    return data


def _clean_urls(urls: Sequence[str]) -> list[str]:
    cleaned: dict[str, str] = {}
    for url in urls:
        try:
            validate_url(url)
            cleaned.setdefault(canonicalize_url(url), url)
        except SourceError as exc:
            raise JobError(f"invalid URL {url[:80]!r}: {exc}") from None
    if not cleaned:
        raise JobError("no URLs were given")
    if len(cleaned) > MAX_URLS:
        raise JobError(f"too many URLs ({len(cleaned)}); the limit is {MAX_URLS} per run")
    return list(cleaned.values())


def run_job(
    urls: Sequence[str],
    *,
    report_date: date,
    db_path: Path | str,
    env: Mapping[str, str] | None = None,
    registry: ProviderRegistry | None = None,
    fetcher: SourceFetcher | None = None,
    submit: Callable[[dict], Mapping[str, object]] | None = None,
    allow_mock: bool = False,
) -> JobResult:
    """Fetch, analyse, validate and (if ``submit`` is given) submit a draft. Raises JobError on any failed check."""
    env = os.environ if env is None else env
    urls = _clean_urls(urls)
    try:
        config = AnalysisConfig.from_env(env)
    except ValueError as exc:
        raise JobError(f"invalid analysis configuration: {exc}") from None
    mocked = sorted(role.value for role, rc in config.roles.items() if rc.provider == "mock")
    if mocked and not allow_mock:
        raise JobError(
            f"roles configured with the mock provider: {', '.join(mocked)}. Refusing to create a draft from "
            "placeholder output; configure real providers (or pass --allow-mock for a dry run)."
        )

    sources = SourceStore(db_path)
    runs = RunStore(db_path)
    own_fetcher = fetcher is None
    fetcher = fetcher or SourceFetcher(sources)
    ok, failed = [], []
    try:
        for url in urls:
            record = fetcher.fetch(url)
            (ok if record.fetch_status is FetchStatus.OK else failed).append(record)
    finally:
        if own_fetcher:
            fetcher.close()
    logger.info("fetched %d source(s), %d failed", len(ok), len(failed))
    if not ok:
        reasons = ", ".join(sorted({f.fetch_status.value for f in failed}))
        raise JobError(f"none of the {len(failed)} source(s) could be fetched ({reasons}); no analysis was run")

    text, id_map = format_sources_for_prompt(ok, failures=failed)
    pipeline = AnalysisPipeline(config, registry or build_registry(env), run_store=runs)
    draft = pipeline.analyze(text, source_map=id_map)
    where = f" (run {draft.run_id})"

    markdown, unknown = expand_citations(draft.report, id_map)
    if unknown:
        raise JobError(f"the report cites source ids that were never provided: {', '.join(unknown)}{where}")
    invented = find_unfetched_urls(markdown, sources)
    if invented:
        raise JobError(f"the report links to {len(invented)} URL(s) that were never fetched, for example {invented[0]}{where}")
    positions = []
    for heading in REQUIRED_HEADINGS:
        match = re.search(rf"^{re.escape(heading)}[ \t]*$", markdown, re.MULTILINE)
        if match is None:
            raise JobError(f"the report is missing the required section '{heading[3:]}'{where}")
        positions.append(match.start())
    if positions != sorted(positions):
        raise JobError(f"the report's sections are not in the required order{where}")
    if len(markdown) > MAX_MARKDOWN_CHARS:
        raise JobError(f"the report is too long ({len(markdown)} characters){where}")

    submitted = False
    if submit is not None:
        reply = submit({
            "report_date": report_date.isoformat(),
            "title": swedish_title(report_date),
            "markdown": markdown,
            "analysis_run_id": draft.run_id,
        })
        if reply.get("status") != "draft":
            raise JobError(f"the submission was not stored as a draft{where}")
        submitted = True
    return JobResult(draft.run_id, report_date, submitted, markdown, draft.warnings, len(ok), len(failed))


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, **overrides) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.analysis.job", description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", help="report date YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--url", action="append", default=[], help="a source URL (repeatable)")
    parser.add_argument("--urls-file", help="file with one URL per line; # starts a comment")
    parser.add_argument("--db", help="SQLite path (default: $DATABASE_PATH)")
    parser.add_argument("--api-url", help="base URL of the publication API (default: $BRIEF_API_URL)")
    parser.add_argument("--no-submit", action="store_true", help="analyse and print the report; submit nothing")
    parser.add_argument("--allow-mock", action="store_true", help="allow mock providers (dry runs and tests only)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    try:
        report_date = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
        urls = [*args.url, *(read_urls(args.urls_file) if args.urls_file else [])]
        submit = overrides.pop("submit", None)
        if submit is None and not args.no_submit:
            token = env.get("INGEST_TOKEN", "").strip()
            if not token:
                raise JobError("INGEST_TOKEN is not set (use --no-submit to only print the report)")
            base = check_api_url(args.api_url or env.get("BRIEF_API_URL") or "http://127.0.0.1:8098")
            submit = lambda payload: submit_via_api(base, token, payload)  # noqa: E731
        result = run_job(
            urls, report_date=report_date, env=env, submit=None if args.no_submit else submit,
            db_path=args.db or env.get("DATABASE_PATH") or "./data/security_brief.sqlite3",
            allow_mock=args.allow_mock, **overrides,
        )
    except (JobError, SourceError, ProviderError, AssessmentError, PromptError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # never print tracebacks: they can carry request data
        print(f"error: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"run {result.run_id}: {result.sources_ok} source(s) used, {result.sources_failed} failed; "
          f"{'draft submitted for editorial review' if result.submitted else 'nothing submitted'}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    if args.no_submit:
        print("---")
        print(result.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
