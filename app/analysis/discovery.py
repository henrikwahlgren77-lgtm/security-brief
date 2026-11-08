"""Source discovery: find today's candidate pages on approved sources.

    python -m app.analysis.discovery --check-feeds      # verify the feed list (writes nothing)
    python -m app.analysis.discovery --out urls.txt     # discover and write a URL list

Discovery reads feeds (RSS/Atom) and index pages listed in ``config/feeds.toml``, keeps items
that are recent, new, on approved hosts and relevant, ranks them, and returns URLs. It never
calls a model and never treats a feed as evidence: the chosen URLs are fetched, stored and
checked like any other source by the normal pipeline.

Feeds are untrusted input. They are fetched through the same guarded fetcher as sources
(https only, approved hosts, public addresses only, size and redirect limits), parsed by a
strict parser that rejects any DOCTYPE or entity declaration, and all text taken from them is
stripped of control characters before it is stored or printed.

Known limits: a feed that is not well-formed XML is reported as failed rather than guessed at;
no sitemaps; relevance is a transparent keyword match, not understanding; two feeds covering
the same story under different URLs are not merged.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
import tomllib
import unicodedata
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterator, Sequence
from urllib.parse import urldefrag, urljoin, urlsplit
from xml.parsers import expat

from .allowlist import Allowlist
from .fetcher import FetchedDocument, SourceFetcher
from .sources import FetchStatus, SourceError, SourceStore, canonicalize_url, validate_url

logger = logging.getLogger("app.analysis.discovery")

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "feeds.toml"
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "application/rdf+xml")
INDEX_TYPES = ("text/html", "application/xhtml+xml")
MAX_ITEMS_PER_FEED = 500
MAX_XML_DEPTH = 40
MAX_FIELD_CHARS = 2000
DEFAULT_WINDOW_HOURS = 36
DEFAULT_MAX_URLS = 20
FUTURE_TOLERANCE = timedelta(days=1)

DEFAULT_KEYWORDS = (
    "militär", "försvar", "försvarsmakt", "totalförsvar", "beredskap", "säkerhetspolit", "säkerhetsläge", "hot", "hybrid",
    "sabotage", "kabel", "infrastruktur", "gps", "störning", "desinformation", "påverkan", "spionage", "underrätt",
    "cyber", "it-angrepp", "dataintrång", "ransomware", "sårbarhet", "terror", "nato", "ryssland", "östersjön", "gotland",
    "baltik", "artikel 5", "sanktion", "kustbevakning", "luftrum",
    "military", "defence", "defense", "security", "threat", "russia", "baltic", "sabotage", "cable", "jamming",
    "disinformation", "espionage", "intelligence", "cyber", "attack", "article 5", "sanction", "airspace",
    "forsvar", "sikkerhet", "trussel", "russland", "østersjøen", "trussel", "puolustus", "turvallisuus", "uhka", "venäjä",
    "julgeolek", "kaitse", "venemaa", "drošība", "aizsardzība", "krievija", "saugumas", "gynyba", "rusija",
)


class DiscoveryError(RuntimeError):
    """Configuration or processing problem; messages never contain feed text."""


class FeedParseError(DiscoveryError):
    pass


# --- text safety ----------------------------------------------------------------------

def clean_text(value: str | None, limit: int = 300) -> str:
    """One line, no control, format or bidi-override characters, bounded length."""
    if not value:
        return ""
    kept = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C") or ch in "\t\n\r")
    return " ".join(kept.split())[:limit]


def _strip_markup(text: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", text)).split())


def parse_feed_date(value: str | None) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom) to an aware UTC datetime; None if it cannot be read."""
    if not value or not value.strip():
        return None
    text = value.strip()
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --- feed and index parsing ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FeedItem:
    title: str
    url: str
    published: datetime | None = None
    summary: str = ""


def parse_feed(data: bytes) -> list[FeedItem]:
    """Parse RSS 2.0, RSS 1.0 or Atom with Python's expat, refusing DOCTYPE and entity declarations."""
    parser = expat.ParserCreate(namespace_separator="|")
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def refuse(*_args) -> None:
        raise FeedParseError("DOCTYPE and entity declarations are not accepted")

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse  # redundant with the DOCTYPE refusal above (an entity can only be declared inside one); kept as a second line
    items: list[FeedItem] = []
    state = {"depth": 0, "item_depth": 0, "in_item": False, "root": None, "field": None}
    fields: dict[str, str] = {}
    buffer: list[str] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        local = name.rsplit("|", 1)[-1]
        state["depth"] += 1
        if state["depth"] > MAX_XML_DEPTH:
            raise FeedParseError("XML nesting is too deep")
        if state["root"] is None:
            state["root"] = local
            if local not in ("rss", "feed", "RDF"):
                raise FeedParseError("not an RSS or Atom feed")
        if local in ("item", "entry") and not state["in_item"]:
            state.update(in_item=True, item_depth=state["depth"], field=None)
            fields.clear()
        elif state["in_item"] and state["depth"] == state["item_depth"] + 1:
            state["field"] = local
            buffer.clear()
            href = attrs.get("href")
            if local == "link" and href and attrs.get("rel", "alternate") == "alternate":
                fields.setdefault("link_href", href)

    def end(name: str) -> None:
        local = name.rsplit("|", 1)[-1]
        if state["in_item"] and state["depth"] == state["item_depth"] + 1 and state["field"] == local:
            text = "".join(buffer).strip()
            if text:
                fields.setdefault(local, text[:MAX_FIELD_CHARS])
            state["field"] = None
        if state["in_item"] and local in ("item", "entry") and state["depth"] == state["item_depth"]:
            url = (fields.get("link_href") or fields.get("link") or "").strip()
            date_text = fields.get("published") or fields.get("pubDate") or fields.get("date") or fields.get("updated")
            summary = fields.get("description") or fields.get("summary") or fields.get("content") or ""
            if url and len(items) < MAX_ITEMS_PER_FEED:
                items.append(FeedItem(clean_text(_strip_markup(fields.get("title", ""))), url,
                                      parse_feed_date(date_text), clean_text(_strip_markup(summary), 500)))
            state["in_item"] = False
        state["depth"] -= 1

    def chars(data_: str) -> None:
        if state["in_item"] and state["field"] is not None and sum(map(len, buffer)) < MAX_FIELD_CHARS:
            buffer.append(data_)

    parser.StartElementHandler, parser.EndElementHandler, parser.CharacterDataHandler = start, end, chars
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise FeedParseError(f"not well-formed XML ({expat.ErrorString(exc.code)})") from None
    if state["root"] is None:
        raise FeedParseError("empty document")
    return items


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or None
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def parse_index(data: bytes, base_url: str, include: re.Pattern[str]) -> list[FeedItem]:
    """Links on an index page whose path (and query) match ``include``. No dates: those are read later."""
    page = _Links()
    page.feed(data.decode("utf-8", errors="replace"))
    seen: set[str] = set()
    items: list[FeedItem] = []
    for href, text in page.links:
        absolute = urldefrag(urljoin(base_url, href.strip()))[0]
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or absolute in seen:
            continue
        if not include.search(parts.path + ("?" + parts.query if parts.query else "")):
            continue
        seen.add(absolute)
        items.append(FeedItem(clean_text(text), absolute))
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
    return items


# --- configuration ------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    url: str
    kind: str = "feed"  # "feed" (RSS/Atom) or "html" (index page)
    relevance: str = "keywords"  # "keywords" (needs a keyword hit) or "all"
    include: re.Pattern[str] | None = None
    max_items: int = 50


@dataclass(frozen=True, slots=True)
class DiscoverySettings:
    feeds: tuple[FeedConfig, ...] = ()
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    window_hours: int = DEFAULT_WINDOW_HOURS
    max_urls: int = DEFAULT_MAX_URLS


def load_settings(path: Path | str | None = None, allowlist: Allowlist | None = None) -> DiscoverySettings:
    allowlist = allowlist or Allowlist.load()
    data = tomllib.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    settings = data.get("settings", {})
    window = int(settings.get("window_hours", DEFAULT_WINDOW_HOURS))
    max_urls = int(settings.get("max_urls", DEFAULT_MAX_URLS))
    if not 1 <= window <= 24 * 14 or not 1 <= max_urls <= 30:
        raise DiscoveryError("settings: window_hours must be 1-336 and max_urls 1-30")
    keywords = data.get("keywords", {}).get("terms") or list(DEFAULT_KEYWORDS)
    if not all(isinstance(k, str) and 2 <= len(k.strip()) <= 40 for k in keywords):
        raise DiscoveryError("keywords: each term must be 2-40 characters")
    feeds: list[FeedConfig] = []
    names: set[str] = set()
    for entry in data.get("feed", []):
        name = str(entry.get("name", "")).strip()
        label = f"feed {name!r}" if name else "a feed without a name"
        if not name or name in names:
            raise DiscoveryError(f"{label}: needs a unique name")
        names.add(name)
        if not entry.get("enabled", True):
            continue
        url = str(entry.get("url", ""))
        try:
            validate_url(url)
        except SourceError as exc:
            raise DiscoveryError(f"{label}: invalid URL ({exc})") from None
        if urlsplit(url).scheme != "https":
            raise DiscoveryError(f"{label}: only https feeds are allowed")
        if allowlist.match(url) is None:
            raise DiscoveryError(f"{label}: host is not on the approved source list (config/approved_sources.toml)")
        kind = entry.get("kind", "feed")
        relevance = entry.get("relevance", "keywords")
        if kind not in ("feed", "html") or relevance not in ("keywords", "all"):
            raise DiscoveryError(f"{label}: kind must be feed|html and relevance keywords|all")
        include = None
        if kind == "html":
            pattern = str(entry.get("include", ""))
            if not pattern or len(pattern) > 200:
                raise DiscoveryError(f"{label}: an html index needs an 'include' pattern (at most 200 characters)")
            try:
                include = re.compile(pattern)
            except re.error:
                raise DiscoveryError(f"{label}: 'include' is not a valid regular expression") from None
        max_items = int(entry.get("max_items", 50))
        if not 1 <= max_items <= 200:
            raise DiscoveryError(f"{label}: max_items must be 1-200")
        feeds.append(FeedConfig(name, url, kind, relevance, include, max_items))
    return DiscoverySettings(tuple(feeds), tuple(k.strip() for k in keywords), window, max_urls)


# --- store ---------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Candidate:
    url: str
    canonical_url: str
    title: str
    feed: str
    published: datetime | None
    tier: int | None
    score: int
    hits: tuple[str, ...]
    decision: str  # "selected" | "skipped"
    reason: str


@dataclass(frozen=True, slots=True)
class FeedReport:
    feed: FeedConfig
    status: FetchStatus
    http_status: int | None
    items: int
    dated: int
    newest: datetime | None
    error: str | None


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    run_id: str
    started_at: datetime
    selected: tuple[Candidate, ...]
    skipped: tuple[Candidate, ...]
    feeds: tuple[FeedReport, ...]

    @property
    def urls(self) -> list[str]:
        return [c.url for c in self.selected]

    @property
    def skip_reasons(self) -> Counter:
        return Counter(c.reason for c in self.skipped)


class DiscoveryStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS discovery_runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, window_hours INTEGER NOT NULL, max_urls INTEGER NOT NULL,
                feeds_ok INTEGER NOT NULL, feeds_failed INTEGER NOT NULL, selected INTEGER NOT NULL, skipped INTEGER NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS discovery_feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
                status TEXT NOT NULL, http_status INTEGER, items INTEGER NOT NULL, dated INTEGER NOT NULL,
                newest TEXT, error TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS discovery_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, feed TEXT NOT NULL, url TEXT NOT NULL,
                canonical_url TEXT NOT NULL, title TEXT, published TEXT, tier INTEGER, score INTEGER NOT NULL,
                hits TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL)""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def save(self, result: DiscoveryResult, window_hours: int, max_urls: int) -> None:
        with self._connect() as c:
            c.execute("INSERT INTO discovery_runs VALUES (?,?,?,?,?,?,?,?)", (
                result.run_id, result.started_at.isoformat(), window_hours, max_urls,
                sum(f.status is FetchStatus.OK for f in result.feeds), sum(f.status is not FetchStatus.OK for f in result.feeds),
                len(result.selected), len(result.skipped)))
            for f in result.feeds:
                c.execute("INSERT INTO discovery_feeds (run_id,name,url,status,http_status,items,dated,newest,error) VALUES (?,?,?,?,?,?,?,?,?)",
                          (result.run_id, f.feed.name, f.feed.url, f.status.value, f.http_status, f.items, f.dated,
                           f.newest.isoformat() if f.newest else None, f.error))
            for item in (*result.selected, *result.skipped):
                c.execute("INSERT INTO discovery_items (run_id,feed,url,canonical_url,title,published,tier,score,hits,decision,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (result.run_id, item.feed, item.url, item.canonical_url, item.title,
                           item.published.isoformat() if item.published else None, item.tier, item.score,
                           ", ".join(item.hits), item.decision, item.reason))

    def last_run(self) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM discovery_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            if row is None:
                return None
            run = dict(row)
            run["items"] = [dict(r) for r in c.execute(
                "SELECT * FROM discovery_items WHERE run_id = ? ORDER BY decision ASC, score DESC, id", (row["id"],))]
            run["feeds"] = [dict(r) for r in c.execute("SELECT * FROM discovery_feeds WHERE run_id = ? ORDER BY id", (row["id"],))]
        return run


# --- discovery -------------------------------------------------------------------------------

class Discovery:
    def __init__(
        self,
        fetcher: SourceFetcher,
        settings: DiscoverySettings,
        sources: SourceStore,
        store: DiscoveryStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.fetcher, self.settings, self.sources, self.store, self.clock = fetcher, settings, sources, store, clock
        self._keywords = [(k, re.compile(r"(?<!\w)" + re.escape(k), re.IGNORECASE)) for k in settings.keywords]

    # -- one feed ---------------------------------------------------------------
    def _read(self, feed: FeedConfig) -> tuple[FeedReport, list[FeedItem]]:
        doc: FetchedDocument = self.fetcher.fetch_document(feed.url, FEED_TYPES if feed.kind == "feed" else INDEX_TYPES)
        if doc.status is not FetchStatus.OK:
            return FeedReport(feed, doc.status, doc.http_status, 0, 0, None, doc.error), []
        try:
            items = parse_feed(doc.body) if feed.kind == "feed" else parse_index(doc.body, doc.final_url or feed.url, feed.include)
        except DiscoveryError as exc:
            return FeedReport(feed, FetchStatus.ERROR, doc.http_status, 0, 0, None, str(exc)), []
        dates = [i.published for i in items if i.published]
        return FeedReport(feed, FetchStatus.OK, doc.http_status, len(items), len(dates), max(dates) if dates else None, None), items

    def check_feeds(self) -> list[FeedReport]:
        """Fetch and parse every configured feed and report what was found. Stores nothing."""
        return [self._read(feed)[0] for feed in self.settings.feeds]

    # -- scoring and selection ------------------------------------------------------
    def _hits(self, item: FeedItem) -> tuple[str, ...]:
        haystack = f"{item.title} {item.summary}"
        return tuple(k for k, pattern in self._keywords if pattern.search(haystack))

    def run(self, *, window_hours: int | None = None, max_urls: int | None = None, include_fetched: bool = False) -> DiscoveryResult:
        if not self.settings.feeds:
            raise DiscoveryError("no feeds are configured: add [[feed]] entries to config/feeds.toml (see docs/source-discovery.md)")
        window_hours = window_hours or self.settings.window_hours
        max_urls = max_urls or self.settings.max_urls
        now = self.clock()
        window_start = now - timedelta(hours=window_hours)
        reports: list[FeedReport] = []
        passed: list[Candidate] = []
        skipped: list[Candidate] = []
        seen: set[str] = set()

        for feed in self.settings.feeds:
            report, items = self._read(feed)
            reports.append(report)
            logger.info("feed %s: %s, %d item(s)", feed.name, report.status.value, report.items)
            ordered = sorted(items, key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            for item in ordered[: feed.max_items] if feed.kind == "feed" else items[: feed.max_items]:
                candidate = self._decide(item, feed, now, window_start, include_fetched, seen)
                (passed if candidate.decision == "selected" else skipped).append(candidate)

        passed.sort(key=lambda c: (-c.score, -(c.published.timestamp() if c.published else 0), c.url))
        selected = passed[:max_urls]
        for extra in passed[max_urls:]:
            skipped.append(replace(extra, decision="skipped", reason="over the per-run limit"))
        result = DiscoveryResult(uuid.uuid4().hex, now, tuple(selected), tuple(skipped), tuple(reports))
        if self.store:
            self.store.save(result, window_hours, max_urls)
        logger.info("discovery %s: %d selected, %d skipped", result.run_id, len(selected), len(skipped))
        return result

    def _decide(self, item: FeedItem, feed: FeedConfig, now: datetime, window_start: datetime,
                include_fetched: bool, seen: set[str]) -> Candidate:
        def verdict(decision: str, reason: str, *, canonical: str = "", tier: int | None = None, score: int = 0,
                    hits: tuple[str, ...] = ()) -> Candidate:
            return Candidate(item.url[:2048], canonical, item.title, feed.name, item.published, tier, score, hits, decision, reason)

        try:
            validate_url(item.url)
            canonical = canonicalize_url(item.url)
        except SourceError:
            return verdict("skipped", "not a valid URL")
        approved = self.fetcher.allowlist.match(item.url)
        if urlsplit(item.url).scheme != "https":
            return verdict("skipped", "not https", canonical=canonical)
        if approved is None:
            return verdict("skipped", "host is not approved", canonical=canonical)
        if canonical in seen:
            return verdict("skipped", "duplicate of another item", canonical=canonical, tier=approved.tier)
        published = item.published
        if published and published > now + FUTURE_TOLERANCE:
            published = None  # a date that far ahead is not a date we can trust
        if published and published < window_start:
            return verdict("skipped", "older than the window", canonical=canonical, tier=approved.tier)
        if not include_fetched and self.sources.was_fetched_before(canonical, window_start):
            return verdict("skipped", "already used in an earlier run", canonical=canonical, tier=approved.tier)
        hits = self._hits(item)
        if feed.relevance == "keywords" and not hits and feed.kind == "feed":
            return verdict("skipped", "no keyword match", canonical=canonical, tier=approved.tier)
        score = len(hits) + (2 if approved.tier == 1 else 0) + (1 if published and now - published <= timedelta(hours=12) else 0) - (0 if published else 1)
        seen.add(canonical)
        reason = "selected" if published else "selected (no date: age unverified)"
        return Candidate(item.url[:2048], canonical, item.title, feed.name, published, approved.tier, score, hits, "selected", reason)


# --- output ------------------------------------------------------------------------------------

def write_urls_file(path: Path | str, result: DiscoveryResult) -> None:
    """One URL per line, in the format the analysis job reads. Titles go in sanitised comment lines."""
    lines = [f"# discovery run {result.run_id}, {result.started_at.isoformat()}"]
    for c in result.selected:
        lines.append(f"# {clean_text(c.feed, 80)} | {clean_text(c.title, 140)} | score {c.score}")
        lines.append(c.url)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def discover_urls(db_path: Path | str, config_path: Path | str | None = None, *, fetcher: SourceFetcher | None = None,
                  include_fetched: bool = False) -> DiscoveryResult:
    """Run discovery against the database at ``db_path`` (used by the job's --discover)."""
    settings = load_settings(config_path)
    sources = SourceStore(db_path)
    own = fetcher is None
    fetcher = fetcher or SourceFetcher(sources)
    try:
        return Discovery(fetcher, settings, sources, DiscoveryStore(db_path)).run(include_fetched=include_fetched)
    finally:
        if own:
            fetcher.close()


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"


def main(argv: Sequence[str] | None = None, *, env: dict[str, str] | None = None, **overrides) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.analysis.discovery", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", help="feeds file (default: config/feeds.toml)")
    parser.add_argument("--db", help="SQLite path (default: $DATABASE_PATH)")
    parser.add_argument("--hours", type=int, help="look back this many hours (default from the config)")
    parser.add_argument("--max", type=int, dest="max_urls", help="select at most this many URLs")
    parser.add_argument("--out", help="write the selected URLs to this file (job-compatible)")
    parser.add_argument("--check-feeds", action="store_true", help="fetch and parse every feed and report; store nothing")
    parser.add_argument("--include-fetched", action="store_true", help="do not skip pages already used in earlier runs")
    parser.add_argument("--last", action="store_true", help="show the last run's decisions and reasons")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db_path = args.db or env.get("DATABASE_PATH") or "./data/security_brief.sqlite3"
    try:
        if args.last:
            run = DiscoveryStore(db_path).last_run()
            if run is None:
                print("No discovery run recorded yet.")
                return 0
            print(f"run {run['id']} {run['started_at']}: {run['selected']} selected, {run['skipped']} skipped, "
                  f"{run['feeds_ok']} feed(s) ok, {run['feeds_failed']} failed")
            for item in run["items"]:
                print(f"  {item['decision']:<8} {item['reason']:<38} {clean_text(item['title'], 70)}  {item['url']}")
            return 0
        settings = load_settings(args.config)
        sources = SourceStore(db_path)
        fetcher = overrides.get("fetcher") or SourceFetcher(sources)
        try:
            discovery = Discovery(fetcher, settings, sources, None if args.check_feeds else DiscoveryStore(db_path))
            if args.check_feeds:
                if not settings.feeds:
                    raise DiscoveryError("no feeds are configured: add [[feed]] entries to config/feeds.toml")
                bad = 0
                for r in discovery.check_feeds():
                    ok = r.status is FetchStatus.OK
                    bad += not ok
                    print(f"{'OK  ' if ok else 'FAIL'} {r.feed.name}: {r.status.value}"
                          + (f" HTTP {r.http_status}" if r.http_status else "")
                          + (f", {r.items} item(s), {r.dated} with a date, newest {_fmt_date(r.newest)}" if ok else f" ({clean_text(r.error)})"))
                return 1 if bad else 0
            result = discovery.run(window_hours=args.hours, max_urls=args.max_urls, include_fetched=args.include_fetched)
        finally:
            if "fetcher" not in overrides:
                fetcher.close()
    except (DiscoveryError, SourceError, OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # never a traceback: it may carry feed text
        print(f"error: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"run {result.run_id}: {len(result.selected)} selected, {len(result.skipped)} skipped; "
          f"{sum(f.status is FetchStatus.OK for f in result.feeds)}/{len(result.feeds)} feed(s) read")
    for reason, count in result.skip_reasons.most_common():
        print(f"  skipped {count:>3}  {reason}")
    for c in result.selected:
        print(f"  + {c.score:>2}  {_fmt_date(c.published):<20} {clean_text(c.title, 70)}  {c.url}")
    if args.out:
        write_urls_file(args.out, result)
        print(f"wrote {len(result.selected)} URL(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
