import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import design_sample, main, pages, presentation as p

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
DESIGN_CSS = (ROOT / "static" / "design.css").read_text(encoding="utf-8")
FONT_CSS = (ROOT / "static" / "fonts" / "fonts.css").read_text(encoding="utf-8")
EDITOR = ("editor", "a-long-test-password")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(main, "ADMIN_USER", "editor")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "a-long-test-password")
    monkeypatch.setattr(main, "INGEST_TOKEN", "test-ingest-token")
    with TestClient(main.app) as test_client:
        yield test_client


def publish_report(client, report_date, markdown=design_sample.LATEST_MARKDOWN, title=None):
    response = client.post("/api/reports", headers={"Authorization": "Bearer test-ingest-token"},
                           json={"report_date": report_date, "title": title or f"Säkerhetsbrief {report_date}", "markdown": markdown})
    assert response.status_code == 200
    published = client.post(f"/admin/reports/{report_date}/publish", data={"csrf_token": main.publish_token(report_date)},
                            auth=EDITOR, follow_redirects=False)
    assert published.status_code == 303


# --- Swedish dates and summer time ------------------------------------------------

def test_swedish_dates():
    assert p.long_date("2026-11-08") == "söndag 8 november 2026"
    assert p.long_date("2026-11-07") == "lördag 7 november 2026"
    assert p.short_date("2026-03-01") == "1 mar 2026" and p.month_label("2026-10") == "oktober 2026"


@pytest.mark.parametrize("utc, expected", [
    ("2026-11-08T06:42:00+00:00", "07:42 CET"), ("2026-07-01T06:00:00+00:00", "08:00 CEST"),
    ("2026-03-29T00:59:00+00:00", "01:59 CET"), ("2026-03-29T01:00:00+00:00", "03:00 CEST"),   # last Sunday of March
    ("2026-10-25T00:59:00+00:00", "02:59 CEST"), ("2026-10-25T01:00:00+00:00", "02:00 CET"),   # last Sunday of October
    ("2026-11-08T06:42:00", "07:42 CET"), (None, ""),
])
def test_stockholm_time_follows_the_eu_summer_time_rule(utc, expected):
    assert p.stockholm_time(utc) == expected


# --- sections, summaries, filters -------------------------------------------------

HTML = "<p>Ingress</p><h2>Verifierade fakta</h2><ul><li>A</li></ul><h2>Okänd rubrik</h2><p>x</p><h2>Okänd rubrik</h2><p>y</p><h2>Källor</h2><ul><li><a href=\"https://kalla.example/1\">K &amp; Co</a></li></ul>"


def test_report_html_is_split_at_the_fixed_headings():
    preface, sections = p.sections_from_html(HTML)
    assert preface == "<p>Ingress</p>"
    assert [(s.kind, s.label) for s in sections] == [("facts", "Bekräftat"), ("other", None), ("other", None), ("sources", None)]
    assert len({s.slug for s in sections}) == 4                       # duplicate headings still get unique anchors
    assert sections[0].html == "<ul><li>A</li></ul>" and "<h2" not in sections[0].html
    assert p.sections_from_html("<p>bara text</p>") == ("<p>bara text</p>", [])


def test_source_hosts_are_added_and_escaped():
    _, sections = p.sections_from_html(HTML)
    html = p.decorate_sources(sections[-1].html)
    assert '<span class="src-host">kalla.example</span>' in html and p.count_links(html) == 1


def test_summary_comes_from_the_summary_bullets():
    md = "## Sammanfattning\n\n- **Fet** punkt med [länk](https://x.example/a).\n- Andra punkten.\n\n## Nästa\n- inte med"
    assert p.summary_points(md) == ["Fet punkt med länk.", "Andra punkten."]
    assert p.summary_points("## Sammanfattning\n\nEtt stycke.\n\nEtt till.") == ["Ett stycke.", "Ett till."]
    assert p.summary_points("## Annat\n- x") == [] and p.plain_summary("ingenting") == ""
    long = "## Sammanfattning\n- " + "ord " * 100
    summary = p.plain_summary(long, 60)
    assert summary.endswith("…") and len(summary) <= 61 and not summary[:-1].endswith(" ")


def test_archive_filter_ignores_values_that_can_not_match():
    rows = design_sample.ROWS
    flt = p.parse_filter(rows, "1999-01", "Påhittat", "  hybrid   ")
    assert (flt.month, flt.topic, flt.q) == ("", "", "hybrid") and flt.active
    assert not p.parse_filter(rows, None, None, None).active
    assert len(p.parse_filter(rows, None, None, "x" * 500).q) == 80
    items = p.archive_items(rows, p.parse_filter(rows, "2026-10", None, None), "/r/")
    assert [i["date"] for i in items] == ["2026-10-31", "2026-10-30", "2026-10-29"]
    assert items[0]["href"] == "/r/2026-10-31"
    cyber = p.archive_items(rows, p.parse_filter(rows, None, "Cyber", None), "/r/")
    assert {i["date"] for i in cyber} == {"2026-11-08", "2026-11-04"}


def test_issue_numbers_follow_publication_order():
    dates = ["2026-11-08", "2026-11-06", "2026-11-07"]
    assert [p.report_number(dates, d) for d in sorted(dates)] == [1, 2, 3] and p.report_number(dates, "2020-01-01") == 0


# --- real routes -----------------------------------------------------------------

def test_an_empty_site_still_has_a_proper_front_page_archive_and_method_page(client):
    assert "Ingen rapport har publicerats ännu" in client.get("/").text
    assert "Inga rapporter matchar" in client.get("/arkiv").text
    method = client.get("/metod")
    assert method.status_code == 200 and "Så tas briefen fram" in method.text


def test_a_published_report_gets_the_new_front_page_report_page_and_archive(client):
    publish_report(client, "2026-11-08")
    home = client.get("/").text
    assert "Utgåva nr 1" in home and "söndag 8 november 2026" in home
    assert "Ett handelsfartyg utan aktiv AIS-signal" in home            # summary bullets are the key developments
    assert 'href="/reports/2026-11-08"' in home and "Läs hela rapporten" in home

    report = client.get("/reports/2026-11-08").text
    for slug in ("sammanfattning", "fakta", "myndigheter", "media", "lagesbild", "analys", "osakerheter", "kallor"):
        assert f'id="{slug}"' in report and f'href="#{slug}"' in report
    assert "label--facts" in report and "label--official" in report and "label--analysis" in report and "label--uncertainty" in report
    assert report.count('class="src-host"') == 6
    archive = client.get("/arkiv").text
    assert "Säkerhetsbrief 2026-11-08" in archive and "Ett handelsfartyg" in archive


def test_drafts_never_appear_in_the_public_archive(client):
    client.post("/api/reports", headers={"Authorization": "Bearer test-ingest-token"},
                json={"report_date": "2026-11-08", "title": "Hemligt utkast", "markdown": "## Sammanfattning\n- x"})
    assert "Hemligt utkast" not in client.get("/arkiv").text and "Hemligt utkast" not in client.get("/").text


def test_archive_filters_by_month_and_text_and_survives_garbage(client):
    publish_report(client, "2026-10-30", "## Sammanfattning\n- Terrorhotet oförändrat.", "Oktoberbrief")
    publish_report(client, "2026-11-08", "## Sammanfattning\n- Kabelfråga i Östersjön.", "Novemberbrief")
    assert "Oktoberbrief" in client.get("/arkiv?manad=2026-10").text and "Novemberbrief" not in client.get("/arkiv?manad=2026-10").text
    both = client.get("/arkiv?manad=<script>&amne=x&q=" + "a" * 500)
    assert both.status_code == 200 and "<script>" not in both.text
    found = client.get("/arkiv?q=kabel").text
    assert "Novemberbrief" in found and "Oktoberbrief" not in found and "1 av 2 rapporter matchar filtret" in found
    assert "Inga rapporter matchar" in client.get("/arkiv?q=finnsinte").text


def test_report_markup_from_a_model_cannot_inject_html_into_the_new_pages(client):
    publish_report(client, "2026-11-08", "## Sammanfattning\n- <script>alert(1)</script> text <img src=x onerror=alert(2)>\n\n## Källor\n- [x](javascript:alert(3))")
    for path in ("/", "/reports/2026-11-08", "/arkiv"):
        page = client.get(path).text
        assert "<script" not in page and "<img" not in page and "onerror" not in page   # tags are stripped with their attributes
        assert 'href="javascript:' not in page


# --- design preview ---------------------------------------------------------------

PREVIEW_PATHS = ["/design/", "/design/rapport/2026-11-08", "/design/rapport/2026-11-05", "/design/arkiv", "/design/metod", "/design/typografi"]


def test_preview_is_off_by_default(client, monkeypatch):
    monkeypatch.setattr(main, "DESIGN_PREVIEW", False)
    for path in ["/design", *PREVIEW_PATHS]:
        assert client.get(path, follow_redirects=False).status_code == 404, path


def test_preview_is_labelled_fictional_noindex_and_stays_inside_itself(client, monkeypatch):
    monkeypatch.setattr(main, "DESIGN_PREVIEW", True)
    assert client.get("/design", follow_redirects=False).status_code == 307
    seen, queue = set(), list(PREVIEW_PATHS)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.text
        if path.startswith("/design"):
            assert "Allt innehåll på den här sidan är påhittat" in html and 'name="robots" content="noindex, nofollow"' in html, path
            for href in re.findall(r'<a [^>]*href="([^"#]+)', html):
                if href.startswith("/"):
                    assert href.startswith("/design"), f"{path} links out of the preview: {href}"
                    queue.append(href.replace("&amp;", "&"))
    assert len(seen) >= 8
    assert client.get("/design/rapport/1999-01-01").status_code == 404


def test_the_real_site_has_no_preview_banner_and_is_indexable(client):
    publish_report(client, "2026-11-08")
    for path in ("/", "/arkiv", "/metod", "/reports/2026-11-08"):
        html = client.get(path).text
        assert "Designexempel" not in html and "noindex" not in html and "/design" not in html


def test_the_sample_content_is_visibly_fictional():
    links = re.findall(r"\]\(([^)]+)\)", design_sample.LATEST_MARKDOWN)
    assert links and all(url.startswith("https://kalla.example/") for url in links)   # reserved .example domain, no real site
    assert "(exempel)" in design_sample.LATEST_MARKDOWN
    assert all("(exempel)" in row["markdown"] for row in design_sample.ROWS)


# --- the CSP: no inline style or script anywhere, no external resources -----------

def all_pages(client, monkeypatch):
    monkeypatch.setattr(main, "DESIGN_PREVIEW", True)
    publish_report(client, "2026-11-08")
    paths = ["/", "/arkiv", "/metod", "/reports/2026-11-08", *PREVIEW_PATHS]
    pages_ = {path: client.get(path) for path in paths}
    pages_["/admin"] = client.get("/admin", auth=EDITOR)
    return pages_


def test_no_page_uses_inline_style_script_or_external_resources(client, monkeypatch):
    for path, response in all_pages(client, monkeypatch).items():
        assert response.status_code == 200, path
        html = response.text
        assert not re.search(r"<style|<script|\sstyle\s*=|\son[a-z]+\s*=|javascript:", html, re.I), path
        assert "src=" not in html, f"{path}: pages load no images or scripts"
        for tag in re.findall(r"<link[^>]+>", html):
            assert "http" not in tag, f"{path}: external link tag {tag}"
        assert "default-src 'self'" in response.headers["content-security-policy"]


def test_every_page_has_semantic_landmarks_and_one_h1(client, monkeypatch):
    for path, response in all_pages(client, monkeypatch).items():
        html = response.text
        assert '<html lang="sv">' in html and html.count("<h1") == 1, path
        assert all(tag in html for tag in ("<header", "<main", "<footer", "<nav")) and 'class="skip"' in html, path
        assert re.search(r"<title>[^<]+</title>", html)


# --- fonts: self-hosted, licensed, only what is used -------------------------------

def test_every_font_file_is_served_referenced_and_licensed(client):
    files = sorted((ROOT / "static" / "fonts").glob("*.woff2"))
    urls = re.findall(r"url\('(/static/fonts/[^']+)'\)", FONT_CSS)
    assert {Path(u).name for u in urls} == {f.name for f in files}, "unused or missing font file"
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200 and len(response.content) > 5_000 and response.content[:4] == b"wOF2", url
    families = set(re.findall(r"font-family: '([^']+)'", FONT_CSS))
    assert families == {"Newsreader", "IBM Plex Sans", "IBM Plex Mono"}
    for stem in ("newsreader", "ibm-plex-sans", "ibm-plex-mono"):
        text = (ROOT / "static" / "fonts" / f"LICENSE-{stem}.txt").read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text and "Copyright" in text
    assert "http" not in FONT_CSS
    assert "fonts.googleapis" not in CSS + FONT_CSS and "fonts.gstatic" not in CSS + FONT_CSS


def test_latin_extended_loads_lazily_so_baltic_names_work_without_a_big_download():
    assert FONT_CSS.count("unicode-range") == FONT_CSS.count("@font-face")
    assert "U+0100-02BA" in FONT_CSS                                   # latin-ext covers š, ž, ā, ė ...
    assert "font-display: swap" in FONT_CSS and FONT_CSS.count("font-display") == FONT_CSS.count("@font-face")


def test_only_the_three_font_roles_are_used_in_the_stylesheets():
    declared = re.findall(r"font-family:\s*([^;]+);", CSS + DESIGN_CSS)
    assert declared and all(re.fullmatch(r"var\(--font-(serif|sans|mono)\)", d.strip()) for d in declared)
    for role in ("serif", "sans", "mono"):
        assert f"--font-{role}:" in CSS
    assert "font-synthesis: none" in CSS


def test_monospace_is_limited_to_metadata_numbers_hosts_and_code():
    used = set()
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        if "var(--font-mono)" in rule.group(2):
            used |= {s.strip() for s in rule.group(1).split(",")}
    assert used == {".meta", ".facts dd", ".report-meta dd", ".developments li::before", ".src-host",
                    ".report-section--sources .prose li::before", ".prose code", ".prose pre"}


# --- design rules: geometry, colour, contrast ---------------------------------------

def luminance(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def token(name):
    return re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", CSS).group(1).upper()


def test_brand_palette_is_present_exactly():
    assert (token("charcoal"), token("orange"), token("grey-100"), token("white")) == ("#282B28", "#D36135", "#F3F3F3", "#FFFFFF")


def test_every_text_colour_reaches_aa_on_white_and_on_grey_and_brand_orange_is_kept_off_text():
    for name in ("charcoal", "orange-ink", "blue", "red", "green", "muted"):
        for background in ("white", "grey-100"):
            assert contrast(token(name), token(background)) >= 4.5, (name, background)
    assert contrast(token("orange"), token("white")) < 4.5                 # why --orange-ink exists
    assert contrast(token("white"), token("charcoal")) >= 12
    assert contrast(token("line-strong"), token("white")) >= 3             # borders that identify form controls
    assert not re.search(r"(?<![\w-])color:\s*var\(--orange\)", CSS), "brand orange must not be used for text colour"


def test_border_radius_stays_between_zero_and_four_pixels():
    for value in re.findall(r"border-radius:\s*([^;]+);", CSS + DESIGN_CSS):
        assert value.strip() in ("var(--radius)", "var(--radius-control)", "0"), value
    assert re.search(r"--radius:\s*0;", CSS) and re.search(r"--radius-control:\s*2px;", CSS)


def test_none_of_the_forbidden_visual_shortcuts_are_used():
    css = (CSS + DESIGN_CSS).lower()
    for banned in ("gradient(", "box-shadow", "backdrop-filter", "filter: blur", "animation:", "@keyframes", "transition:"):
        assert banned not in css, banned
    assert "border-radius: 9999" not in css and "50%" not in re.sub(r"[^;{}]*width[^;{}]*;", "", css)


def test_backgrounds_are_neutral_never_cream_or_beige():
    for color in set(re.findall(r"#[0-9A-Fa-f]{6}", CSS)):
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        if luminance(color) > 0.55:                                        # any light colour
            assert r - b <= 8, f"{color} is warm/cream"


def test_fluid_scales_and_measure_are_defined_with_clamp():
    steps = re.findall(r"--step-(?:-?\d):\s*clamp\(", CSS)
    spaces = re.findall(r"--space-[0-9a-z]+:\s*clamp\(", CSS)
    assert len(steps) >= 8 and len(spaces) >= 8
    assert "--measure: 66ch" in CSS and re.search(r"\.prose\s*\{[^}]*max-width:\s*var\(--measure\)", CSS)


def test_smallest_type_is_twelve_pixels_or_more():
    match = re.search(r"--step--2:\s*clamp\(([\d.]+)rem", CSS)
    assert float(match.group(1)) * 16 >= 12
    sizes = [float(x) for x in re.findall(r"font-size:\s*([\d.]+)px", CSS)]
    assert all(s >= 12 for s in sizes)


def test_analysis_codes_are_shown_to_editors_in_swedish():
    from app.analysis.assessment import (Article5Category, Change, Classification, ClaimKind, ObjectionType, Severity)
    for enum in (Classification, ClaimKind, Change, Article5Category, ObjectionType, Severity):
        for member in enum:
            assert p.term(member.value) != member.value, f"{member.value} has no Swedish name"
    assert p.term("credible_unconfirmed") == "Trovärdig men obekräftad" and p.term("something_new") == "something_new"
