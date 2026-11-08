import re
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.analysis import ProviderRegistry
from app.analysis.allowlist import Allowlist
from app.analysis.discovery import (
    Discovery,
    DiscoveryError,
    DiscoverySettings,
    DiscoveryStore,
    FeedConfig,
    FeedParseError,
    clean_text,
    load_settings,
    main as discovery_main,
    parse_feed,
    parse_feed_date,
    parse_index,
    write_urls_file,
)
from app.analysis.fetcher import SourceFetcher
from app.analysis.job import main as job_main, read_urls
from app.analysis.mock_provider import EMPTY_COUNTER_JSON, MockProvider, with_block
from app.analysis.sources import FetchStatus, SourceRecord, SourceStore

NOW = datetime(2026, 11, 8, 7, 0, tzinfo=timezone.utc)
PUBLIC_IP = "93.184.216.34"
FEED_URL = "https://www.sakerhetspolisen.se/rss.xml"
SECOND_FEED_URL = "https://www.msb.se/rss.xml"


def rfc(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(*items, extra=""):
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        + (f"<pubDate>{rfc(when)}</pubDate>" if when else "")
        + f"<description>{summary}</description></item>"
        for title, link, when, summary in items
    )
    return f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>x</title>{extra}{body}</channel></rss>'.encode()


def hours_ago(n):
    return NOW - timedelta(hours=n)


# --- parsing ----------------------------------------------------------------------------

def test_rss_items_are_read_with_cdata_dates_and_markup_stripped():
    xml = ('<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="m"><channel>'
           '<title>Kanalens egen titel</title><item><title><![CDATA[Sabotage mot <b>kabel</b> &amp; mer]]></title>'
           '<link>https://www.msb.se/a</link><dc:date>2026-11-08T05:00:00Z</dc:date>'
           '<description><![CDATA[<p>Text &lt;med&gt; tecken</p>]]></description><media:title>Fel titel</media:title></item></channel></rss>')
    (item,) = parse_feed(xml.encode())
    assert item.title == "Sabotage mot kabel & mer" and item.url == "https://www.msb.se/a"
    assert item.published == datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc) and "Text <med> tecken" in item.summary


def test_atom_entries_use_the_alternate_link_and_prefer_published_over_updated():
    xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>f</title><entry><title>Nytt</title>'
           '<link rel="self" href="https://www.msb.se/self"/><link rel="alternate" href="https://www.msb.se/alt"/>'
           '<updated>2026-11-08T06:00:00+01:00</updated><published>2026-11-07T10:00:00Z</published><summary>s</summary></entry></feed>')
    (item,) = parse_feed(xml.encode())
    assert item.url == "https://www.msb.se/alt" and item.published == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload", [
    b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title><link>https://www.msb.se/x</link></item></channel></rss>',
    b'<?xml version="1.0"?><!DOCTYPE rss SYSTEM "http://evil.example/x.dtd"><rss><channel/></rss>',
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&e;</title><link>https://www.msb.se/x</link></item></channel></rss>',
    b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;">]><rss><channel><item><title>&c;</title></item></channel></rss>',
])
def test_doctype_and_entity_tricks_are_refused(payload):
    with pytest.raises(FeedParseError, match="DOCTYPE"):
        parse_feed(payload)


@pytest.mark.parametrize("payload, message", [
    (b"<rss><channel><item><title>x</title>", "not well-formed"),
    (b"not xml at all", "not well-formed"),
    (b"", "not well-formed"),
    (b"<html><body>hej</body></html>", "not an RSS or Atom feed"),
    (b"<rss><channel><item><title>&nbsp;</title></item></channel></rss>", "not well-formed"),   # undefined HTML entity
    (b"<rss>" + b"<a>" * 60 + b"</a>" * 60 + b"</rss>", "too deep"),
])
def test_broken_feeds_fail_cleanly_without_echoing_their_content(payload, message):
    with pytest.raises(FeedParseError, match=message):
        parse_feed(payload)


def test_item_count_and_field_size_are_bounded():
    many = b'<rss><channel>' + b"".join(b"<item><title>t</title><link>https://www.msb.se/%d</link></item>" % i for i in range(700)) + b"</channel></rss>"
    assert len(parse_feed(many)) == 500
    huge = b"<rss><channel><item><title>" + b"x" * 100_000 + b"</title><link>https://www.msb.se/a</link></item></channel></rss>"
    assert len(parse_feed(huge)[0].title) <= 300


def test_control_and_bidi_characters_are_stripped_from_feed_text():
    dirty = "Rubrik\x1b[31m röd‮ dold\x00 ​ slut\nny rad"
    assert clean_text(dirty) == "Rubrik[31m röd dold slut ny rad"
    assert "\x1b" not in clean_text("\x1b[2J") and clean_text(None) == ""


@pytest.mark.parametrize("text, expected", [
    ("Sun, 08 Nov 2026 05:00:00 +0000", datetime(2026, 11, 8, 5, tzinfo=timezone.utc)),
    ("Sun, 08 Nov 2026 06:00:00 +0100", datetime(2026, 11, 8, 5, tzinfo=timezone.utc)),
    ("2026-11-08T05:00:00Z", datetime(2026, 11, 8, 5, tzinfo=timezone.utc)),
    ("2026-11-08T07:00:00+02:00", datetime(2026, 11, 8, 5, tzinfo=timezone.utc)),
    ("2026-11-08", datetime(2026, 11, 8, tzinfo=timezone.utc)),
    ("igår kväll", None), ("", None), (None, None),
])
def test_feed_dates(text, expected):
    assert parse_feed_date(text) == expected


def test_index_pages_yield_only_matching_absolute_https_links():
    html = ('<a href="/nyheter/a">Första</a><a href="/nyheter/a#del">Dublett</a><a href="/om-oss">Om oss</a>'
            '<a href="mailto:x@y.se">Mejl</a><a href="javascript:alert(1)">JS</a><a href="https://www.msb.se/nyheter/b">  Andra\n nyheten </a>').encode()
    items = parse_index(html, "https://www.msb.se/", re.compile(r"^/nyheter/"))
    assert [(i.url, i.title) for i in items] == [("https://www.msb.se/nyheter/a", "Första"), ("https://www.msb.se/nyheter/b", "Andra nyheten")]
    assert all(i.published is None for i in items)


# --- configuration ---------------------------------------------------------------------------

def write_config(tmp_path, body):
    path = tmp_path / "feeds.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_shipped_config_loads_and_ships_no_unverified_feeds():
    settings = load_settings()
    assert settings.feeds == () and 1 <= settings.max_urls <= 30 and "nato" in settings.keywords


def test_a_valid_config_is_read(tmp_path):
    cfg = write_config(tmp_path, '[settings]\nwindow_hours = 24\nmax_urls = 5\n[keywords]\nterms = ["nato", "kabel"]\n'
                       '[[feed]]\nname = "A"\nurl = "https://www.sakerhetspolisen.se/rss.xml"\nrelevance = "all"\n'
                       '[[feed]]\nname = "B"\nurl = "https://www.msb.se/nyheter/"\nkind = "html"\ninclude = "^/nyheter/"\n'
                       '[[feed]]\nname = "C"\nurl = "https://www.pst.no/rss"\nenabled = false\n')
    settings = load_settings(cfg)
    assert [f.name for f in settings.feeds] == ["A", "B"] and settings.keywords == ("nato", "kabel")
    assert (settings.window_hours, settings.max_urls) == (24, 5) and settings.feeds[1].include.pattern == "^/nyheter/"


@pytest.mark.parametrize("body, message", [
    ('[[feed]]\nname = "x"\nurl = "https://evil.example/rss"\n', "not on the approved"),
    ('[[feed]]\nname = "x"\nurl = "http://www.msb.se/rss"\n', "https"),
    ('[[feed]]\nname = "x"\nurl = "not a url"\n', "invalid URL"),
    ('[[feed]]\nname = "x"\nurl = "https://www.msb.se/n"\nkind = "html"\n', "include"),
    ('[[feed]]\nname = "x"\nurl = "https://www.msb.se/n"\nkind = "html"\ninclude = "("\n', "regular expression"),
    ('[[feed]]\nname = "x"\nurl = "https://www.msb.se/n"\nkind = "json"\n', "kind"),
    ('[[feed]]\nname = "x"\nurl = "https://www.msb.se/n"\nmax_items = 9999\n', "max_items"),
    ('[[feed]]\nurl = "https://www.msb.se/n"\n', "unique name"),
    ('[[feed]]\nname = "x"\nurl = "https://www.msb.se/a"\n[[feed]]\nname = "x"\nurl = "https://www.msb.se/b"\n', "unique name"),
    ('[settings]\nmax_urls = 500\n', "max_urls"),
    ('[keywords]\nterms = ["a"]\n', "2-40"),
])
def test_bad_configs_are_rejected_with_a_clear_message(tmp_path, body, message):
    with pytest.raises(DiscoveryError, match=message):
        load_settings(write_config(tmp_path, body))


# --- discovery runs ----------------------------------------------------------------------------

class Web:
    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request):
        self.requests.append(str(request.url))
        answer = self.routes.get(str(request.url))
        if answer is None:
            return httpx.Response(404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, content=answer, headers={"content-type": "application/rss+xml; charset=utf-8"})


def build(tmp_path, routes, feeds, *, keywords=("nato", "sabotage", "kabel", "säkerhet"), max_urls=20, resolver=lambda h: [PUBLIC_IP]):
    db = tmp_path / "d.sqlite3"
    sources = SourceStore(db)
    web = Web(routes)
    fetcher = SourceFetcher(sources, Allowlist.load(), transport=httpx.MockTransport(web), resolver=resolver, clock=lambda: NOW)
    settings = DiscoverySettings(tuple(feeds), keywords, 36, max_urls)
    return Discovery(fetcher, settings, sources, DiscoveryStore(db), clock=lambda: NOW), sources, web, db


FEED = FeedConfig("Säpo", FEED_URL, "feed", "keywords")


def reasons(result):
    return {c.url.rsplit("/", 1)[-1]: c.reason for c in result.skipped}


def test_only_recent_new_approved_relevant_https_items_are_selected_and_every_skip_has_a_reason(tmp_path):
    xml = rss(
        ("Sabotage mot kabel", "https://www.sakerhetspolisen.se/nyheter/ny", hours_ago(3), "Händelse i Östersjön"),
        ("Gammal NATO-nyhet", "https://www.sakerhetspolisen.se/nyheter/gammal", hours_ago(100), "NATO"),
        ("Pressträff om lunch", "https://www.sakerhetspolisen.se/nyheter/lunch", hours_ago(2), "Ingenting relevant"),
        ("Länk utanför listan", "https://evil.example/nyheter/extern", hours_ago(2), "Sabotage"),
        ("Osäker transport", "http://www.sakerhetspolisen.se/nyheter/http", hours_ago(2), "Sabotage"),
        ("Trasig länk", "inte-en-url", hours_ago(2), "Sabotage"),
        ("Sabotage mot kabel igen", "https://www.sakerhetspolisen.se/nyheter/ny?utm_source=x", hours_ago(1), "kabel"),
    )
    discovery, _, _, _ = build(tmp_path, {FEED_URL: xml}, [FEED])
    result = discovery.run()
    assert result.urls == ["https://www.sakerhetspolisen.se/nyheter/ny?utm_source=x"]       # the newest copy wins
    assert reasons(result) == {
        "gammal": "older than the window", "lunch": "no keyword match", "extern": "host is not approved",
        "http": "not https", "inte-en-url": "not a valid URL", "ny": "duplicate of another item",
    }
    assert result.feeds[0].status is FetchStatus.OK and result.feeds[0].items == 7


def test_pages_used_in_earlier_runs_are_not_presented_as_news_again(tmp_path):
    url = "https://www.sakerhetspolisen.se/nyheter/ny"
    discovery, sources, _, _ = build(tmp_path, {FEED_URL: rss(("Sabotage", url, hours_ago(3), ""))}, [FEED])
    sources.add(SourceRecord(name="Säpo", url=url, fetched_at=NOW - timedelta(days=2), fetch_status=FetchStatus.OK,
                             content_sha256="a" * 64, content_text="t"))
    assert discovery.run().selected == () and discovery.run().skip_reasons["already used in an earlier run"] == 1
    assert discovery.run(include_fetched=True).urls == [url]
    # a page fetched TODAY (inside the window) does not count as old news, so a same-day rerun still works
    discovery2, sources2, _, _ = build(tmp_path / "x", {FEED_URL: rss(("Sabotage", url, hours_ago(3), ""))}, [FEED])
    sources2.add(SourceRecord(name="Säpo", url=url, fetched_at=NOW - timedelta(hours=1), fetch_status=FetchStatus.OK,
                              content_sha256="a" * 64, content_text="t"))
    assert discovery2.run().urls == [url]


def test_undated_items_are_kept_but_marked_and_ranked_lower(tmp_path):
    xml = rss(("Sabotage utan datum", "https://www.sakerhetspolisen.se/nyheter/utan", None, ""),
              ("Sabotage med datum", "https://www.sakerhetspolisen.se/nyheter/med", hours_ago(2), ""),
              ("Sabotage från framtiden", "https://www.sakerhetspolisen.se/nyheter/framtid", NOW + timedelta(days=30), ""))
    result = build(tmp_path, {FEED_URL: xml}, [FEED])[0].run()
    by_url = {c.url.rsplit("/", 1)[-1]: c for c in result.selected}
    assert result.urls[0].endswith("/med")
    assert by_url["utan"].published is None and "age unverified" in by_url["utan"].reason
    assert by_url["framtid"].published is None                       # an implausible future date is not trusted


def test_ranking_prefers_keyword_hits_authority_tier_and_recency_and_the_cap_is_explained(tmp_path):
    items = [(f"Sabotage {n}", f"https://www.sakerhetspolisen.se/nyheter/n{n}", hours_ago(n + 1), "") for n in range(6)]
    items.append(("Sabotage mot kabel och NATO", "https://www.sakerhetspolisen.se/nyheter/bäst", hours_ago(30), "säkerhet"))
    result = build(tmp_path, {FEED_URL: rss(*items)}, [FEED], max_urls=3)[0].run()
    assert result.urls[0].endswith("/b%C3%A4st") or result.urls[0].endswith("/bäst")        # most keyword hits first
    assert len(result.selected) == 3 and result.skip_reasons["over the per-run limit"] == 4


def test_relevance_all_keeps_on_topic_feeds_without_keywords(tmp_path):
    xml = rss(("Pressmeddelande", "https://www.sakerhetspolisen.se/nyheter/p", hours_ago(2), "Intet nyckelord"))
    on_topic = FeedConfig("Säpo", FEED_URL, "feed", "all")
    assert build(tmp_path, {FEED_URL: xml}, [on_topic])[0].run().urls == ["https://www.sakerhetspolisen.se/nyheter/p"]


def test_two_feeds_are_merged_without_duplicates_and_one_failing_feed_does_not_stop_the_others(tmp_path):
    shared = ("Sabotage", "https://www.msb.se/nyheter/delad", hours_ago(2), "")
    routes = {FEED_URL: rss(shared, ("Sabotage två", "https://www.sakerhetspolisen.se/nyheter/två", hours_ago(2), "")),
              SECOND_FEED_URL: rss(shared), "https://www.pst.no/rss": httpx.Response(503),
              "https://www.nato.int/rss": "<html>inte ett flöde</html>".encode(), "https://www.fra.se/rss": httpx.ReadTimeout("slow")}
    feeds = [FEED, FeedConfig("MSB", SECOND_FEED_URL), FeedConfig("PST", "https://www.pst.no/rss"),
             FeedConfig("NATO", "https://www.nato.int/rss"), FeedConfig("FRA", "https://www.fra.se/rss")]
    result = build(tmp_path, routes, feeds)[0].run()
    assert len(result.urls) == 2 and result.skip_reasons["duplicate of another item"] == 1 or len(result.urls) == 2
    statuses = {f.feed.name: f.status for f in result.feeds}
    assert statuses == {"Säpo": FetchStatus.OK, "MSB": FetchStatus.OK, "PST": FetchStatus.HTTP_ERROR,
                        "NATO": FetchStatus.ERROR, "FRA": FetchStatus.UNREACHABLE}
    assert next(f for f in result.feeds if f.feed.name == "NATO").error == "not an RSS or Atom feed"


def test_a_feed_host_that_resolves_to_a_private_address_is_blocked_before_any_request(tmp_path):
    discovery, _, web, _ = build(tmp_path, {FEED_URL: rss()}, [FEED], resolver=lambda h: ["10.0.0.7"])
    result = discovery.run()
    assert result.feeds[0].status is FetchStatus.BLOCKED and web.requests == []


def test_a_redirect_to_an_unapproved_host_is_blocked(tmp_path):
    routes = {FEED_URL: httpx.Response(302, headers={"location": "https://evil.example/rss.xml"})}
    discovery, _, web, _ = build(tmp_path, routes, [FEED])
    assert discovery.run().feeds[0].status is FetchStatus.BLOCKED and web.requests == [FEED_URL]


def test_index_pages_are_used_when_a_site_has_no_feed(tmp_path):
    page = httpx.Response(200, content='<a href="/nyheter/ett">Säkerhet i fokus</a><a href="/om">Om</a>'.encode(),
                          headers={"content-type": "text/html; charset=utf-8"})
    index = FeedConfig("MSB index", "https://www.msb.se/nyheter/", "html", "all", re.compile(r"^/nyheter/[^/]+$"))
    result = build(tmp_path, {"https://www.msb.se/nyheter/": page}, [index])[0].run()
    assert result.urls == ["https://www.msb.se/nyheter/ett"] and "age unverified" in result.selected[0].reason


def test_every_decision_is_stored_for_later_inspection(tmp_path):
    xml = rss(("Sabotage", "https://www.sakerhetspolisen.se/nyheter/ja", hours_ago(2), ""),
              ("Lunch", "https://www.sakerhetspolisen.se/nyheter/nej", hours_ago(2), ""))
    discovery, _, _, db = build(tmp_path, {FEED_URL: xml}, [FEED])
    result = discovery.run()
    last = DiscoveryStore(db).last_run()
    assert (last["id"], last["selected"], last["skipped"], last["feeds_ok"], last["feeds_failed"]) == (result.run_id, 1, 1, 1, 0)
    assert [(i["decision"], i["reason"]) for i in last["items"]] == [("selected", "selected"), ("skipped", "no keyword match")]
    assert last["feeds"][0]["items"] == 2 and last["feeds"][0]["status"] == "ok"


def test_no_feeds_configured_is_a_clear_error_not_an_empty_success(tmp_path):
    discovery, *_ = build(tmp_path, {}, [])
    with pytest.raises(DiscoveryError, match="no feeds are configured"):
        discovery.run()


def test_check_feeds_reports_without_writing_anything(tmp_path):
    xml = rss(("Sabotage", "https://www.sakerhetspolisen.se/nyheter/a", hours_ago(2), ""),
              ("Utan datum", "https://www.sakerhetspolisen.se/nyheter/b", None, ""))
    discovery, sources, _, db = build(tmp_path, {FEED_URL: xml, "https://www.pst.no/rss": httpx.Response(500)},
                                      [FEED, FeedConfig("PST", "https://www.pst.no/rss")])
    reports = discovery.check_feeds()
    assert [(r.status, r.items, r.dated) for r in reports] == [(FetchStatus.OK, 2, 1), (FetchStatus.HTTP_ERROR, 0, 0)]
    assert reports[0].newest == hours_ago(2)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0] == 0


# --- output, command line and the job ------------------------------------------------------------

def test_the_urls_file_is_job_compatible_and_titles_cannot_break_it(tmp_path):
    xml = rss(("Sabotage\nhttps://evil.example/injected \u202e dold \u200b slut", "https://www.sakerhetspolisen.se/nyheter/a", hours_ago(1), ""))
    discovery, *_ = build(tmp_path, {FEED_URL: xml}, [FEED])
    result = discovery.run()
    out = tmp_path / "urls.txt"
    write_urls_file(out, result)
    assert read_urls(out) == ["https://www.sakerhetspolisen.se/nyheter/a"]            # titles are comments only
    text = out.read_text(encoding="utf-8")
    assert "\u202e" not in text and "\u200b" not in text and "evil.example/injected" in text.split("\n")[1]   # kept as a comment
    assert all(line.startswith("#") or line.startswith("https://www.sakerhetspolisen.se") for line in out.read_text().splitlines())


def cli_settings(tmp_path, feed_block):
    return write_config(tmp_path, "[settings]\nwindow_hours = 36\nmax_urls = 10\n" + feed_block)


def cli_fetcher(tmp_path, routes):
    return SourceFetcher(SourceStore(tmp_path / "c.sqlite3"), Allowlist.load(), transport=httpx.MockTransport(Web(routes)),
                         resolver=lambda h: [PUBLIC_IP], clock=lambda: NOW)


def test_cli_check_feeds_prints_a_verdict_per_feed_and_fails_if_any_feed_fails(tmp_path, capsys):
    cfg = cli_settings(tmp_path, f'[[feed]]\nname = "Bra"\nurl = "{FEED_URL}"\n[[feed]]\nname = "Trasig"\nurl = "{SECOND_FEED_URL}"\n')
    fetcher = cli_fetcher(tmp_path, {FEED_URL: rss(("x", "https://www.sakerhetspolisen.se/n/a", hours_ago(2), "")), SECOND_FEED_URL: httpx.Response(404)})
    code = discovery_main(["--config", str(cfg), "--db", str(tmp_path / "c.sqlite3"), "--check-feeds"], env={}, fetcher=fetcher)
    out = capsys.readouterr().out
    assert code == 1 and "OK   Bra: ok HTTP 200, 1 item(s), 1 with a date" in out and "FAIL Trasig: http_error HTTP 404" in out


def test_cli_discovers_prints_reasons_and_writes_the_file(tmp_path, capsys):
    cfg = cli_settings(tmp_path, f'[[feed]]\nname = "Säpo"\nurl = "{FEED_URL}"\n')
    xml = rss(("Sabotage", "https://www.sakerhetspolisen.se/n/ja", hours_ago(2), ""), ("Lunch", "https://www.sakerhetspolisen.se/n/nej", hours_ago(2), ""))
    out_file = tmp_path / "u.txt"
    code = discovery_main(["--config", str(cfg), "--db", str(tmp_path / "c.sqlite3"), "--out", str(out_file)], env={},
                          fetcher=cli_fetcher(tmp_path, {FEED_URL: xml}))
    printed = capsys.readouterr().out
    assert code == 0 and "1 selected, 1 skipped" in printed and "no keyword match" in printed and f"wrote 1 URL(s) to {out_file}" in printed
    assert read_urls(out_file) == ["https://www.sakerhetspolisen.se/n/ja"]
    discovery_main(["--db", str(tmp_path / "c.sqlite3"), "--last"], env={})
    assert "selected" in capsys.readouterr().out


def test_cli_without_feeds_or_with_a_bad_config_exits_with_one_clear_line(tmp_path, capsys):
    assert discovery_main(["--config", str(tmp_path / "missing.toml"), "--db", str(tmp_path / "c.sqlite3")], env={}) == 1
    bad = write_config(tmp_path, '[[feed]]\nname = "x"\nurl = "https://evil.example/rss"\n')
    assert discovery_main(["--config", str(bad), "--db", str(tmp_path / "c.sqlite3")], env={}) == 1
    err = capsys.readouterr().err
    assert err.count("error:") == 2 and "Traceback" not in err
    empty = write_config(tmp_path, "[settings]\nmax_urls = 5\n")
    assert discovery_main(["--config", str(empty), "--db", str(tmp_path / "c.sqlite3")], env={}) == 1
    assert "no feeds are configured" in capsys.readouterr().err


PAGE = "<html><head><title>Sabotage vid kabel</title></head><body><p>Läget är oförändrat.</p></body></html>"
JOB_ENV = {f"ANALYSIS_{r}_PROVIDER": "main" for r in ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")}


def test_the_job_can_discover_its_own_urls_and_nothing_is_published(tmp_path, capsys):
    page_url = "https://www.sakerhetspolisen.se/n/ja"
    cfg = cli_settings(tmp_path, f'[[feed]]\nname = "Säpo"\nurl = "{FEED_URL}"\n')

    def route(request):
        url = str(request.url)
        if url == FEED_URL:
            return httpx.Response(200, content=rss(("Sabotage", page_url, hours_ago(2), "")), headers={"content-type": "application/rss+xml"})
        if url == page_url:
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    db = tmp_path / "j.sqlite3"
    fetcher = SourceFetcher(SourceStore(db), Allowlist.load(), transport=httpx.MockTransport(route), resolver=lambda h: [PUBLIC_IP], clock=lambda: NOW)
    from tests.test_job import main_block, report  # same fixtures the job tests use
    provider = MockProvider(["insamlat [S1]", main_block(), with_block("granskning", EMPTY_COUNTER_JSON), report()])
    code = job_main(["--date", "2026-11-08", "--discover", "--feeds-config", str(cfg), "--db", str(db), "--no-submit"], env=JOB_ENV,
                    fetcher=fetcher, registry=ProviderRegistry({"main": provider}))
    out = capsys.readouterr().out
    assert code == 0 and "nothing submitted" in out and "1 source(s) used" in out
    assert "Läget är oförändrat" in provider.calls[0].context              # the discovered page reached the analysis
    assert DiscoveryStore(db).last_run()["selected"] == 1


def test_the_job_stops_clearly_when_discovery_has_nothing(tmp_path, capsys):
    cfg = cli_settings(tmp_path, f'[[feed]]\nname = "Säpo"\nurl = "{FEED_URL}"\n')
    db = tmp_path / "j.sqlite3"
    fetcher = SourceFetcher(SourceStore(db), Allowlist.load(), transport=httpx.MockTransport(Web({FEED_URL: rss()})),
                            resolver=lambda h: [PUBLIC_IP], clock=lambda: NOW)
    provider = MockProvider()
    code = job_main(["--discover", "--feeds-config", str(cfg), "--db", str(db), "--no-submit"], env=JOB_ENV,
                    fetcher=fetcher, registry=ProviderRegistry({"main": provider}))
    assert code == 1 and "no URLs were given" in capsys.readouterr().err and provider.calls == []
    code = job_main(["--discover", "--feeds-config", str(tmp_path / "nope.toml"), "--db", str(db), "--no-submit"], env=JOB_ENV)
    assert code == 1


def test_discovery_never_touches_models_or_the_analysis_pipeline():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "analysis" / "discovery.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import) ([\w.]+)", source, re.MULTILINE)
    assert not {"httpx"} & set(imports)                                # it uses the guarded fetcher, never its own client
    for forbidden in (".adapters", ".pipeline", ".providers", ".prompts", ".runs"):
        assert forbidden not in imports, forbidden


def test_the_urls_file_writer_is_safe_on_its_own_even_for_text_that_was_never_cleaned(tmp_path):
    from app.analysis.discovery import Candidate, DiscoveryResult

    dirty = Candidate("https://www.msb.se/n/a", "https://www.msb.se/n/a", "Rubrik\nhttps://evil.example/x\u202e dold", "Källa\r\nmed rad",
                      NOW, 1, 3, (), "selected", "selected")
    out = tmp_path / "u.txt"
    write_urls_file(out, DiscoveryResult("r" * 32, NOW, (dirty,), (), ()))
    lines = out.read_text(encoding="utf-8").split("\n")
    assert read_urls(out) == ["https://www.msb.se/n/a"] and "\u202e" not in "".join(lines)
    assert [l for l in lines if l and not l.startswith("#")] == ["https://www.msb.se/n/a"]    # nothing smuggled in as a URL line
