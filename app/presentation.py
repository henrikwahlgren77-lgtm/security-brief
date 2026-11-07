"""Presentation helpers: Swedish dates, report sections, summaries and archive filtering.

Pure functions, no web framework and no database. Reports stay plain Markdown in
the database; the fixed section headings the publication uses are what let the
templates give each kind of statement its own visual treatment.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape, unescape
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

_WEEKDAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
_MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
           "oktober", "november", "december"]
_MONTHS_SHORT = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


# --- dates ----------------------------------------------------------------------

def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def long_date(value: date | str) -> str:
    d = _as_date(value)
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}"


def short_date(value: date | str) -> str:
    d = _as_date(value)
    return f"{d.day} {_MONTHS_SHORT[d.month - 1]} {d.year}"


def month_label(value: str) -> str:
    year, month = value.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


def _last_sunday(year: int, month: int) -> date:
    d = date(year, month, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def stockholm_time(value: str | datetime | None) -> str:
    """'07:42 CET' for a UTC timestamp, applying the EU summer-time rule (no tz database needed)."""
    if not value:
        return ""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    start = datetime.combine(_last_sunday(moment.year, 3), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(moment.year, 10), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    summer = start <= moment < end
    local = moment + timedelta(hours=2 if summer else 1)
    return f"{local:%H:%M} {'CEST' if summer else 'CET'}"


# --- report sections --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SectionKind:
    kind: str
    label: str | None  # visible text label, never colour alone
    note: str | None


# Keys are the publication's fixed headings, case-folded.
KNOWN_SECTIONS: dict[str, SectionKind] = {
    "sammanfattning": SectionKind("summary", None, None),
    "verifierade fakta": SectionKind("facts", "Bekräftat", "Bekräftat av primärkälla eller tillräckligt oberoende verifiering."),
    "myndigheters och regeringars bedömningar": SectionKind(
        "official", "Myndighetsbedömning",
        "Vad myndigheter och regeringar själva bedömer eller hävdar. Det är inte detsamma som oberoende verifierat."),
    "mediarapportering": SectionKind("media", "Medierapportering", "Rapporterat men inte bekräftat av någon primärkälla."),
    "säkerhetsläget i norden och östersjöregionen": SectionKind("situation", None, None),
    "redaktionell analys": SectionKind("analysis", "Analys", "Publikationens egen bedömning. Håll den isär från fakta."),
    "osäkerheter och luckor": SectionKind("uncertainty", "Osäkerhet", "Vad som är oklart, motstridigt eller inte gick att verifiera."),
    "källor": SectionKind("sources", None, None),
}
_SLUGS = {"sammanfattning": "sammanfattning", "verifierade fakta": "fakta", "myndigheters och regeringars bedömningar": "myndigheter",
          "mediarapportering": "media", "säkerhetsläget i norden och östersjöregionen": "lagesbild",
          "redaktionell analys": "analys", "osäkerheter och luckor": "osakerheter", "källor": "kallor"}


@dataclass(frozen=True, slots=True)
class Section:
    slug: str
    title: str
    kind: str
    label: str | None
    note: str | None
    html: str  # the section body, without its own heading

    @property
    def text(self) -> str:
        return html_to_text(self.html)


_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())


def _slugify(text: str) -> str:
    table = str.maketrans("åäöéü", "aaoeu")
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold().translate(table)).strip("-")
    return slug or "avsnitt"


def sections_from_html(html: str) -> tuple[str, list[Section]]:
    """Split sanitised report HTML at its <h2> headings. Returns (text before the first heading, sections)."""
    parts = re.split(r"(?=<h2[^>]*>)", html)
    preface = parts[0] if parts and not parts[0].lstrip().startswith("<h2") else ""
    chunks = [p for p in parts if p.lstrip().startswith("<h2")]
    sections: list[Section] = []
    used: set[str] = set()
    for chunk in chunks:
        match = _H2.match(chunk.lstrip())
        if match is None:
            continue
        title = " ".join(unescape(_TAG.sub("", match.group(1))).split())
        key = title.casefold()
        known = KNOWN_SECTIONS.get(key)
        slug = _SLUGS.get(key) or _slugify(title)
        while slug in used:
            slug += "-2"
        used.add(slug)
        sections.append(Section(
            slug=slug, title=title, kind=known.kind if known else "other", label=known.label if known else None,
            note=known.note if known else None, html=chunk.lstrip()[match.end():].strip()))
    return preface.strip(), sections


_LINK = re.compile(r'<a href="(https?://[^"]+)"([^>]*)>(.*?)</a>', re.DOTALL)


def decorate_sources(html: str) -> str:
    """Add the host name under each link in the source list, so a reader sees where it leads."""
    def add_host(match: re.Match[str]) -> str:
        host = urlsplit(unescape(match.group(1))).hostname or ""
        return f'{match.group(0)}<span class="src-host">{escape(host)}</span>' if host else match.group(0)
    return _LINK.sub(add_host, html)


def count_links(html: str) -> int:
    return len(_LINK.findall(html))


def reading_minutes(html: str, words_per_minute: int = 200) -> int:
    return max(1, math.ceil(len(html_to_text(html).split()) / words_per_minute))


def excerpt(text: str, limit: int = 420) -> str:
    """Plain text cut at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def first_paragraph_text(html: str, limit: int = 420) -> str:
    match = re.search(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
    return excerpt(html_to_text(match.group(1) if match else html), limit)


# --- Markdown summaries -----------------------------------------------------------

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")


def plain_markdown(text: str) -> str:
    text = _TAG.sub("", text)  # raw HTML in the Markdown is never shown, not even as text
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"[*_`#>]+", "", text)
    return " ".join(text.split())


def markdown_section(markdown: str, heading: str) -> str | None:
    pattern = re.compile(rf"^##[ \t]+{re.escape(heading)}[ \t]*$", re.MULTILINE | re.IGNORECASE)
    start = pattern.search(markdown)
    if start is None:
        return None
    following = re.search(r"^##[ \t]+", markdown[start.end():], re.MULTILINE)
    end = start.end() + following.start() if following else len(markdown)
    return markdown[start.end():end].strip()


def summary_points(markdown: str, limit: int = 6) -> list[str]:
    """The bullets of 'Sammanfattning' as plain text (or its paragraphs if it has no bullets)."""
    body = markdown_section(markdown, "Sammanfattning")
    if not body:
        return []
    bullets = [plain_markdown(m.group(1)) for line in body.splitlines() if (m := _BULLET.match(line))]
    if not bullets:
        bullets = [plain_markdown(p) for p in re.split(r"\n\s*\n", body) if p.strip()]
    return [b for b in bullets if b][:limit]


def plain_summary(markdown: str, limit: int = 240) -> str:
    points = summary_points(markdown, 2)
    if not points:
        return ""
    text = points[0] if len(points[0]) >= 110 or len(points) == 1 else f"{points[0]} {points[1]}"
    return excerpt(text, limit)


# --- archive ------------------------------------------------------------------------

def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def row_topics(row: Any) -> tuple[str, ...]:
    raw = _get(row, "topics", ())
    return tuple(raw) if not isinstance(raw, str) else tuple(t.strip() for t in raw.split(",") if t.strip())


def available_months(rows: Iterable[Any]) -> list[tuple[str, str]]:
    months = sorted({str(_get(r, "report_date"))[:7] for r in rows}, reverse=True)
    return [(m, month_label(m)) for m in months]


def available_topics(rows: Iterable[Any]) -> list[str]:
    return sorted({t for r in rows for t in row_topics(r)})


@dataclass(frozen=True, slots=True)
class ArchiveFilter:
    month: str = ""
    topic: str = ""
    q: str = ""

    @property
    def active(self) -> bool:
        return bool(self.month or self.topic or self.q)


def parse_filter(rows: Sequence[Any], month: str | None, topic: str | None, q: str | None) -> ArchiveFilter:
    """Accept only values that can actually match; anything else is ignored rather than trusted."""
    months = {m for m, _ in available_months(rows)}
    topics = set(available_topics(rows))
    return ArchiveFilter(
        month=month if month in months else "",
        topic=topic if topic in topics else "",
        q=" ".join((q or "").split())[:80],
    )


def archive_items(rows: Sequence[Any], flt: ArchiveFilter, href_prefix: str) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        report_date = str(_get(row, "report_date"))
        summary = plain_summary(str(_get(row, "markdown", "")))
        topics = row_topics(row)
        title = str(_get(row, "title", ""))
        if flt.month and not report_date.startswith(flt.month):
            continue
        if flt.topic and flt.topic not in topics:
            continue
        if flt.q and flt.q.casefold() not in f"{title} {summary}".casefold():
            continue
        items.append({"date": report_date, "title": title, "summary": summary, "topics": topics,
                      "href": f"{href_prefix}{report_date}"})
    return sorted(items, key=lambda i: i["date"], reverse=True)


def report_number(published_dates: Iterable[str], report_date: str) -> int:
    return sorted(published_dates).index(report_date) + 1 if report_date in set(published_dates) else 0


# --- Swedish names for the analysis codes shown to editors ---------------------------

TERMS = {
    # severity
    "decisive": "Avgörande", "substantial": "Väsentlig", "minor": "Mindre",
    # objection type
    "unsupported": "Källstöd saknas", "misclassified": "Felklassificerad", "conflation": "Sammanblandning", "time": "Tid",
    "alternative": "Alternativ tolkning", "overstated": "Överdriven", "understated": "Underskattad",
    "contradiction": "Motstridig", "missing": "Saknas", "article5": "Artikel 5", "numeric_risk": "Risksiffra",
    # claim kind
    "fact": "Sakuppgift", "official_assessment": "Myndighetsbedömning", "media_report": "Medierapportering", "own_analysis": "Egen analys",
    # classification
    "confirmed": "Bekräftad", "credible_unconfirmed": "Trovärdig men obekräftad", "disputed": "Omstridd", "unconfirmed": "Obekräftad",
    # change
    "new": "Ny", "changed": "Förändrad", "continuing": "Fortsatt utveckling", "confirmation": "Bekräftelse", "denial": "Dementi",
    "background": "Bakgrund", "no_change": "Oförändrad", "no_comparison": "Ingen jämförelse",
    # article 5 category
    "attack": "Direkt attack mot NATO-land", "serious_incident": "Allvarlig incident", "hybrid_activity": "Hybridaktivitet",
    "rhetoric": "Retorik utan militär handling", "no_basis": "Inget underlag",
}


def term(value: Any) -> str:
    """Swedish name for an analysis code; unknown values pass through unchanged."""
    return TERMS.get(str(value), str(value))
