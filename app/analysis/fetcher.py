"""Fetch pages from approved sources and record exactly what happened.

Safety properties:
- only https, standard port, no embedded credentials;
- the host (and every redirect target) must be on the approved list;
- hosts that resolve to non-public addresses are refused (SSRF guard);
- redirects are followed manually, at most ``MAX_REDIRECTS``;
- hard limits on time and response size;
- every outcome, including failure, is stored as a ``SourceRecord``.

Known limit: the address check resolves the name before the request and the
HTTP client resolves it again, so a hostile DNS server could in theory answer
differently the second time (DNS rebinding). Approved hosts are authorities
we trust, and the job runs without access to internal services; do not widen
the allowlist to untrusted domains without pinning connections.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from .allowlist import Allowlist
from .sources import (
    FetchStatus,
    OriginType,
    SourceError,
    SourceRecord,
    SourceStore,
    SourceTopic,
    validate_url,
)

MAX_REDIRECTS = 3
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 100_000
TIMEOUT_SECONDS = 15.0
USER_AGENT = "NorthernEuropeSecurityBrief/0.1 (+https://brief.kinetic.nu; public-source research)"
ACCEPTED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class _Blocked(Exception):
    pass


class _Page(HTMLParser):
    """Extract visible text, title and a stated publication time from HTML."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _DATE_KEYS = {
        "article:published_time", "og:article:published_time", "datepublished", "date",
        "pubdate", "publishdate", "dc.date", "dc.date.issued", "dcterms.created", "dcterms.issued",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.published: str | None = None
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            if key in self._DATE_KEYS and self.published is None:
                self.published = attrs.get("content") or None
            if key in ("og:title",) and not self.title_parts:
                self.title_parts.append(attrs.get("content", ""))
        elif tag in self._SKIP and tag != "head":
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP and tag != "head" and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip_depth and data.strip():
            self.text_parts.append(data.strip())


def parse_iso_utc(value: str | None) -> str | None:
    """Normalise a stated timestamp to ISO 8601 UTC, or None if unparseable."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def extract_page(html: str) -> tuple[str | None, str | None, str]:
    page = _Page()
    page.feed(html)
    title = " ".join(" ".join(page.title_parts).split()) or None
    text = "\n".join(page.text_parts)
    return title, parse_iso_utc(page.published), text


class SourceFetcher:
    def __init__(
        self,
        store: SourceStore,
        allowlist: Allowlist | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = system_resolver,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.store = store
        self.allowlist = allowlist or Allowlist.load()
        self.resolver = resolver
        self.clock = clock
        self._client = httpx.Client(
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _check_target(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise _Blocked("only https URLs are fetched")
        if parts.port not in (None, 443):
            raise _Blocked("only the standard https port is allowed")
        if self.allowlist.match_host(parts.hostname) is None:
            raise _Blocked("host is not on the approved source list")
        try:
            addresses = self.resolver(parts.hostname or "")
        except OSError as exc:
            raise ConnectionError(f"DNS lookup failed: {exc}") from exc
        if not addresses:
            raise ConnectionError("DNS lookup returned no addresses")
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise _Blocked("host resolves to a non-public address")

    def _get(self, url: str) -> tuple[httpx.Response, bytes, str]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._check_target(current)
            with self._client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise _Blocked("redirect without a Location header")
                    current = urljoin(current, location)
                    validate_url(current)
                    continue
                chunks, size = [], 0
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if response.status_code == 200 and content_type not in ACCEPTED_TYPES:
                    raise ValueError(f"unsupported content type: {content_type or 'unknown'}")
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("response exceeds the size limit")
                    chunks.append(chunk)
                return response, b"".join(chunks), current
        raise _Blocked("too many redirects")

    def fetch(
        self,
        url: str,
        *,
        topic: SourceTopic | None = None,
        origin_type: OriginType = OriginType.ORIGINAL,
        original_url: str | None = None,
        event_date: str | None = None,
        run_id: str | None = None,
    ) -> SourceRecord:
        """Fetch ``url`` and store the outcome. Raises SourceError only for a malformed URL."""
        validate_url(url)
        approved = self.allowlist.match(url)
        fetched_at = self.clock()
        base = dict(
            url=url,
            fetched_at=fetched_at,
            name=approved.name if approved else (urlsplit(url).hostname or url),
            country=approved.country if approved else None,
            region=approved.region if approved else None,
            tier=approved.tier if approved else None,
            topic=topic,
            origin_type=origin_type,
            original_url=original_url,
            event_date=event_date,
            run_id=run_id,
        )

        def failed(status: FetchStatus, error: str, http_status: int | None = None) -> SourceRecord:
            return self.store.add(SourceRecord(fetch_status=status, error=error, http_status=http_status, **base))

        try:
            response, body, _final_url = self._get(url)
        except _Blocked as exc:
            return failed(FetchStatus.BLOCKED, str(exc))
        except (httpx.TimeoutException, httpx.TransportError, ConnectionError) as exc:
            return failed(FetchStatus.UNREACHABLE, f"{type(exc).__name__}: {exc}"[:300])
        except (ValueError, SourceError) as exc:
            return failed(FetchStatus.ERROR, str(exc)[:300])

        if response.status_code != 200:
            return failed(FetchStatus.HTTP_ERROR, f"HTTP {response.status_code}", response.status_code)

        charset = response.charset_encoding or "utf-8"
        try:
            html = body.decode(charset, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        title, published_at, text = extract_page(html) if "html" in response.headers.get(
            "content-type", ""
        ) else (None, None, html)
        text = text.strip()
        if not text:
            return failed(FetchStatus.ERROR, "page contained no extractable text", 200)

        return self.store.add(
            SourceRecord(
                fetch_status=FetchStatus.OK,
                http_status=200,
                title=title,
                published_at=published_at,
                content_sha256=hashlib.sha256(body).hexdigest(),
                content_text=text[:MAX_TEXT_CHARS],
                **base,
            )
        )
