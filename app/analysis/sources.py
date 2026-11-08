"""Source records: what was fetched, when, from where, and how it was checked.

A report may only cite URLs that exist here with ``fetch_status == "ok"``.
Nothing in this module calls the network or a model.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src"}
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'\]\)]+", re.IGNORECASE)


class FetchStatus(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"  # refused before any request (not approved, unsafe target)
    UNREACHABLE = "unreachable"  # timeout / connection / DNS failure
    HTTP_ERROR = "http_error"  # server answered with an error status
    ERROR = "error"  # unsupported content, too large, parse failure


class OriginType(StrEnum):
    ORIGINAL = "original"
    REPUBLICATION = "republication"


class SourceTopic(StrEnum):
    MILITARY = "military"
    RUSSIAN_MILITARY = "russian_military"
    NATO = "nato"
    HYBRID = "hybrid"
    CYBER = "cyber"
    TERRORISM = "terrorism"
    POLICY = "policy"
    OTHER = "other"


class SourceError(ValueError):
    """A source record or URL is invalid."""


def validate_url(url: str) -> str:
    """Return the URL unchanged if it is a plausible absolute http(s) URL."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise SourceError("URL is empty or too long")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise SourceError("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError as exc:
        raise SourceError(f"Invalid URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise SourceError("Only http and https URLs are allowed")
    if not host or "." not in host.strip("."):
        raise SourceError("URL has no valid host name")
    if parts.username or parts.password:
        raise SourceError("URLs with embedded credentials are not allowed")
    return url


def canonicalize_url(url: str) -> str:
    """Normalise a URL so trivially different forms compare equal.

    Lower-cases scheme and host, drops fragments, default ports and common
    tracking parameters, sorts the query and removes a trailing slash.
    This is a de-duplication heuristic, not proof that two pages are identical.
    """
    validate_url(url)
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One fetch attempt of one URL."""

    name: str
    url: str
    fetched_at: datetime
    fetch_status: FetchStatus
    title: str | None = None
    published_at: str | None = None  # ISO 8601 UTC; only if the page states it
    event_date: str | None = None  # ISO date; only if established, never guessed
    country: str | None = None
    region: str | None = None
    topic: SourceTopic | None = None
    origin_type: OriginType = OriginType.ORIGINAL
    original_url: str | None = None  # for republications: the underlying source
    tier: int | None = None
    http_status: int | None = None
    error: str | None = None
    content_sha256: str | None = None
    content_text: str | None = None
    summary: str | None = None  # filled by the research step, never by the fetcher
    run_id: str | None = None
    id: int | None = None

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)


def _validate_record(record: SourceRecord) -> None:
    validate_url(record.url)
    if not record.name.strip():
        raise SourceError("A source needs a name")
    if record.fetched_at.tzinfo is None:
        raise SourceError("fetched_at must be timezone-aware")
    if record.original_url is not None:
        validate_url(record.original_url)
    if record.origin_type is OriginType.REPUBLICATION and not record.original_url:
        raise SourceError("A republication must name its original_url")
    if record.fetch_status is FetchStatus.OK and not record.content_sha256:
        raise SourceError("A successfully fetched source must have a content hash")
    if record.fetch_status is not FetchStatus.OK and not record.error:
        raise SourceError("A failed fetch must record why it failed")


class SourceStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    title TEXT,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    published_at TEXT,
                    event_date TEXT,
                    country TEXT,
                    region TEXT,
                    topic TEXT,
                    origin_type TEXT NOT NULL,
                    original_url TEXT,
                    tier INTEGER,
                    fetched_at TEXT NOT NULL,
                    fetch_status TEXT NOT NULL,
                    http_status INTEGER,
                    error TEXT,
                    content_sha256 TEXT,
                    content_text TEXT,
                    summary TEXT,
                    run_id TEXT
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS sources_canonical ON sources (canonical_url)")
            connection.execute("CREATE INDEX IF NOT EXISTS sources_hash ON sources (content_sha256)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def add(self, record: SourceRecord) -> SourceRecord:
        _validate_record(record)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sources (name, title, url, canonical_url, published_at, event_date, country,
                    region, topic, origin_type, original_url, tier, fetched_at, fetch_status, http_status,
                    error, content_sha256, content_text, summary, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.name, record.title, record.url, record.canonical_url, record.published_at,
                    record.event_date, record.country, record.region,
                    record.topic.value if record.topic else None, record.origin_type.value,
                    record.original_url, record.tier,
                    record.fetched_at.astimezone(timezone.utc).isoformat(), record.fetch_status.value,
                    record.http_status, record.error, record.content_sha256, record.content_text,
                    record.summary, record.run_id,
                ),
            )
        return replace(record, id=cursor.lastrowid)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            id=row["id"], name=row["name"], title=row["title"], url=row["url"],
            published_at=row["published_at"], event_date=row["event_date"], country=row["country"],
            region=row["region"], topic=SourceTopic(row["topic"]) if row["topic"] else None,
            origin_type=OriginType(row["origin_type"]), original_url=row["original_url"], tier=row["tier"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]), fetch_status=FetchStatus(row["fetch_status"]),
            http_status=row["http_status"], error=row["error"], content_sha256=row["content_sha256"],
            content_text=row["content_text"], summary=row["summary"], run_id=row["run_id"],
        )

    def get(self, source_id: int) -> SourceRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._from_row(row) if row else None

    def latest_ok(self, url: str) -> SourceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE canonical_url = ? AND fetch_status = 'ok' "
                "ORDER BY fetched_at DESC, id DESC LIMIT 1",
                (canonicalize_url(url),),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_ok(self, since: datetime | None = None) -> list[SourceRecord]:
        query = "SELECT * FROM sources WHERE fetch_status = 'ok'"
        params: tuple = ()
        if since is not None:
            if since.tzinfo is None:
                raise SourceError("since must be timezone-aware")
            query += " AND fetched_at >= ?"
            params = (since.astimezone(timezone.utc).isoformat(),)
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY fetched_at, id", params).fetchall()
        return [self._from_row(row) for row in rows]

    def list_failures(self) -> list[SourceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sources WHERE fetch_status != 'ok' ORDER BY fetched_at, id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_summary(self, source_id: int, summary: str) -> None:
        if not summary.strip():
            raise SourceError("Summary must not be empty")
        with self._connect() as connection:
            connection.execute("UPDATE sources SET summary = ? WHERE id = ?", (summary.strip(), source_id))

    def was_fetched_before(self, url: str, before: datetime) -> bool:
        """True if ``url`` was successfully fetched at some time earlier than ``before``."""
        if before.tzinfo is None:
            raise SourceError("before must be timezone-aware")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sources WHERE canonical_url = ? AND fetch_status = 'ok' AND fetched_at < ? LIMIT 1",
                (canonicalize_url(url), before.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return row is not None

    def fetched_urls(self) -> set[str]:
        """Canonical URLs that have at least one successful fetch."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT canonical_url FROM sources WHERE fetch_status = 'ok'"
            ).fetchall()
        return {row["canonical_url"] for row in rows}


def independent_origins(records: Iterable[SourceRecord]) -> dict[str, list[SourceRecord]]:
    """Group records by the origin they actually rest on.

    Two outlets republishing the same press release or wire story, or two
    URLs with identical content, share one group and count as ONE independent
    source. The key is the canonical URL of the original.
    """
    groups: dict[str, list[SourceRecord]] = {}
    key_by_hash: dict[str, str] = {}
    for record in records:
        if record.fetch_status is not FetchStatus.OK:
            continue
        if record.origin_type is OriginType.REPUBLICATION and record.original_url:
            key = canonicalize_url(record.original_url)
        else:
            key = record.canonical_url
        if record.content_sha256:
            key = key_by_hash.setdefault(record.content_sha256, key)
        groups.setdefault(key, []).append(record)
    return groups


def find_unfetched_urls(text: str, store: SourceStore) -> list[str]:
    """URLs in ``text`` that have no successful, recorded fetch.

    Used to reject model output that cites sources the system never retrieved.
    """
    known = store.fetched_urls()
    missing: list[str] = []
    for raw in _URL_PATTERN.findall(text):
        url = raw.rstrip(".,;:!?*_")
        try:
            canonical = canonicalize_url(url)
        except SourceError:
            canonical = None
        if canonical is None or canonical not in known:
            if url not in missing:
                missing.append(url)
    return missing
