"""Build template contexts for the public pages.

Used by both the real routes and the design preview, so the mockup is the same
templates and the same code as production, fed with different rows.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from . import presentation as p

REAL_URLS = {"home": "/", "archive": "/arkiv", "method": "/metod", "report_prefix": "/reports/", "typography": ""}
PREVIEW_URLS = {"home": "/design/", "archive": "/design/arkiv", "method": "/design/metod",
                "report_prefix": "/design/rapport/", "typography": "/design/typografi"}

Render = Callable[[str], str]


def _report_facts(sections: Sequence[p.Section], html: str) -> dict[str, int]:
    sources = next((s for s in sections if s.kind == "sources"), None)
    return {"sources": p.count_links(sources.html) if sources else 0, "minutes": p.reading_minutes(html)}


def home_context(rows: Sequence[Any], render: Render, urls: Mapping[str, str], extras: Mapping[str, Any] | None = None) -> dict[str, Any]:
    extras = extras or {}
    latest = rows[0] if rows else None
    context: dict[str, Any] = {"urls": urls, "section": "home", "latest": latest, "total": len(rows), "recent": [], "report_href": ""}
    if latest is None:
        return context
    html = render(latest["markdown"])
    _, sections = p.sections_from_html(html)
    by_kind = {s.kind: s for s in sections}
    date_key = str(latest["report_date"])
    developments = extras.get("developments") or [
        {"kind": None, "label": None, "text": point} for point in p.summary_points(latest["markdown"])]
    context.update(
        headline=extras.get("headline") or latest["title"],
        standfirst=extras.get("standfirst"),
        developments=developments,
        regions=extras.get("regions"),
        changes=extras.get("changes"),
        situation_text=p.first_paragraph_text(by_kind["situation"].html, 520) if "situation" in by_kind else "",
        analysis_text=p.first_paragraph_text(by_kind["analysis"].html, 560) if "analysis" in by_kind else "",
        report_facts=_report_facts(sections, html),
        report_href=f"{urls['report_prefix']}{date_key}",
        published_time=p.stockholm_time(p._get(latest, "published_at")),
        number=p.report_number([str(r["report_date"]) for r in rows], date_key),
        recent=p.archive_items(rows[1:], p.ArchiveFilter(), urls["report_prefix"])[:5],
    )
    return context


def report_context(report: Any, rows: Sequence[Any], render: Render, urls: Mapping[str, str], extras: Mapping[str, Any] | None = None) -> dict[str, Any]:
    extras = extras or {}
    html = render(report["markdown"])
    preface, sections = p.sections_from_html(html)
    sections = [
        p.Section(s.slug, s.title, s.kind, s.label, s.note, p.decorate_sources(s.html) if s.kind == "sources" else s.html)
        for s in sections
    ]
    date_key = str(report["report_date"])
    return {
        "urls": urls, "section": "archive", "report": report, "preface": preface, "sections": sections,
        "facts": _report_facts(sections, html),
        "headline": extras.get("headline"), "standfirst": extras.get("standfirst"),
        "published_time": p.stockholm_time(p._get(report, "published_at")),
        "number": p.report_number([str(r["report_date"]) for r in rows], date_key),
    }


def archive_context(rows: Sequence[Any], urls: Mapping[str, str], manad: str | None, amne: str | None, q: str | None) -> dict[str, Any]:
    flt = p.parse_filter(rows, manad, amne, q)
    return {
        "urls": urls, "section": "archive", "flt": flt, "months": p.available_months(rows),
        "topics": p.available_topics(rows), "items": p.archive_items(rows, flt, urls["report_prefix"]), "total": len(rows),
    }


# --- typography comparison (design preview only) ----------------------------------

DIRECTIONS = [
    {"id": "a", "name": "Modern redaktionell", "recommended": True,
     "fonts": "Newsreader 400/500 för rubrik, ingress och analys · IBM Plex Sans 400/500/600 för brödtext och gränssnitt · IBM Plex Mono 400/500 för metadata"},
    {"id": "b", "name": "Nordisk och funktionell", "recommended": False,
     "fonts": "IBM Plex Sans 400/500/600 genomgående · IBM Plex Mono 400/500 för metadata"},
    {"id": "c", "name": "Internationell säkerhetspublikation", "recommended": False,
     "fonts": "Newsreader 400/500 för rubrik och brödtext · IBM Plex Sans för etiketter och navigering · IBM Plex Mono för metadata"},
]

# Measured from the woff2 files (latin subset, weight 400) with fontTools; see docs/design-research.md.
MEASURED = [
    {"name": "Newsreader", "x": "0.426", "cap": "0.670", "sv": "ja", "license": "OFL 1.1", "verdict": "Vald: rubriker, ingress, analys"},
    {"name": "IBM Plex Sans", "x": "0.516", "cap": "0.698", "sv": "ja", "license": "OFL 1.1", "verdict": "Vald: brödtext, etiketter, gränssnitt"},
    {"name": "IBM Plex Mono", "x": "0.516", "cap": "0.698", "sv": "ja", "license": "OFL 1.1", "verdict": "Vald: bara datum och metadata"},
    {"name": "Source Sans 3", "x": "0.486", "cap": "0.660", "sv": "ja", "license": "OFL 1.1", "verdict": "Avstår: bra men utan egen särart"},
    {"name": "Public Sans", "x": "0.517", "cap": "0.723", "sv": "ja", "license": "OFL 1.1", "verdict": "Avstår: neutral till det generiska"},
    {"name": "Geist", "x": "0.530", "cap": "0.710", "sv": "ja", "license": "OFL 1.1", "verdict": "Avstår: läses som mjukvaruprodukt"},
    {"name": "Instrument Serif", "x": "0.510", "cap": "0.720", "sv": "ja", "license": "OFL 1.1", "verdict": "Avstår: bara display, en vikt, tål inte sektionsrubriker"},
    {"name": "Switzer", "x": "0.531", "cap": "0.680", "sv": "ja", "license": "ITF Free Font License", "verdict": "Ej införd: ingen verifierad källa att självhysa från (se docs/fonts.md)"},
]


def typography_context(urls: Mapping[str, str]) -> dict[str, Any]:
    return {"urls": urls, "section": "typography", "directions": DIRECTIONS, "measured": MEASURED}
