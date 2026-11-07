from datetime import datetime, timedelta, timezone

import pytest

from app.analysis.allowlist import Allowlist
from app.analysis.sources import (
    FetchStatus,
    OriginType,
    SourceError,
    SourceRecord,
    SourceStore,
    canonicalize_url,
    find_unfetched_urls,
    independent_origins,
    validate_url,
)

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return SourceStore(tmp_path / "sources.sqlite3")


def ok(url, digest="a" * 64, **kwargs):
    defaults = dict(name="Test", url=url, fetched_at=NOW, fetch_status=FetchStatus.OK,
                    content_sha256=digest, content_text="text")
    defaults.update(kwargs)
    return SourceRecord(**defaults)


@pytest.mark.parametrize("url", [
    "ftp://example.org/a", "javascript:alert(1)", "https://", "https://localhost/a",
    "https://user:pw@example.org/", "https://example.org/a b", "/relative/path", "", "https://exa mple.org",
])
def test_invalid_urls_are_rejected(url):
    with pytest.raises(SourceError):
        validate_url(url)


def test_canonical_url_merges_trivial_variants():
    variants = [
        "https://Example.ORG/news/1/",
        "https://example.org:443/news/1?utm_source=x#section",
        "https://example.org/news/1?fbclid=abc",
    ]
    assert len({canonicalize_url(v) for v in variants}) == 1
    assert canonicalize_url("https://example.org/a?b=2&a=1") == canonicalize_url("https://example.org/a?a=1&b=2")
    assert canonicalize_url("https://example.org/a?id=1") != canonicalize_url("https://example.org/a?id=2")


def test_source_requires_real_url_and_registered_fetch(store):
    with pytest.raises(SourceError):
        store.add(ok("not-a-url"))
    with pytest.raises(SourceError):
        store.add(ok("https://example.org/a", fetched_at=datetime(2026, 11, 8)))  # naive time
    with pytest.raises(SourceError):
        store.add(ok("https://example.org/a", digest=None))  # "ok" without evidence
    with pytest.raises(SourceError):
        store.add(SourceRecord(name="x", url="https://example.org/a", fetched_at=NOW,
                               fetch_status=FetchStatus.HTTP_ERROR))  # failure without reason
    with pytest.raises(SourceError):
        store.add(ok("https://example.org/a", origin_type=OriginType.REPUBLICATION))  # no original


def test_roundtrip_keeps_publication_event_and_fetch_times_separate(store):
    saved = store.add(ok(
        "https://example.org/a", published_at="2026-11-01T08:00:00+00:00", event_date="2026-10-30",
        country="SE", region="Norden", title="Rubrik",
    ))
    loaded = store.get(saved.id)
    assert loaded.published_at == "2026-11-01T08:00:00+00:00"
    assert loaded.event_date == "2026-10-30"
    assert loaded.fetched_at == NOW
    assert loaded.country == "SE"


def test_failures_are_registered_and_never_count_as_fetched(store):
    store.add(SourceRecord(name="x", url="https://example.org/down", fetched_at=NOW,
                           fetch_status=FetchStatus.UNREACHABLE, error="timeout"))
    assert [r.error for r in store.list_failures()] == ["timeout"]
    assert store.latest_ok("https://example.org/down") is None
    assert store.fetched_urls() == set()


def test_list_ok_since_excludes_older_fetches(store):
    store.add(ok("https://example.org/old", fetched_at=NOW - timedelta(days=3)))
    store.add(ok("https://example.org/new", fetched_at=NOW - timedelta(hours=2), digest="b" * 64))
    urls = [r.url for r in store.list_ok(since=NOW - timedelta(hours=24))]
    assert urls == ["https://example.org/new"]


def test_summary_is_set_only_by_explicit_call(store):
    saved = store.add(ok("https://example.org/a"))
    assert store.get(saved.id).summary is None
    store.set_summary(saved.id, "  Kort sammanfattning.  ")
    assert store.get(saved.id).summary == "Kort sammanfattning."
    with pytest.raises(SourceError):
        store.set_summary(saved.id, "   ")


def test_republications_of_one_original_count_as_one_independent_source():
    original = "https://authority.example/press/1"
    records = [
        ok("https://outlet-a.example/story", digest="1" * 64, origin_type=OriginType.REPUBLICATION, original_url=original),
        ok("https://outlet-b.example/story", digest="2" * 64, origin_type=OriginType.REPUBLICATION, original_url=original),
        ok(original, digest="3" * 64),
        ok("https://other.example/independent", digest="4" * 64),
    ]
    groups = independent_origins(records)
    assert len(groups) == 2
    assert len(groups[canonicalize_url(original)]) == 3


def test_identical_content_on_different_urls_is_one_source():
    records = [ok("https://a.example/x", digest="9" * 64), ok("https://b.example/y", digest="9" * 64)]
    assert len(independent_origins(records)) == 1


def test_failed_fetches_do_not_form_independent_sources():
    failed = SourceRecord(name="x", url="https://a.example/x", fetched_at=NOW,
                          fetch_status=FetchStatus.ERROR, error="bad")
    assert independent_origins([failed]) == {}


def test_report_may_cite_only_fetched_urls(store):
    store.add(ok("https://authority.example/press/1"))
    store.add(SourceRecord(name="x", url="https://down.example/a", fetched_at=NOW,
                           fetch_status=FetchStatus.HTTP_ERROR, error="HTTP 500"))
    report = (
        "Källa: [Myndigheten](https://authority.example/press/1?utm_source=mail).\n"
        "Se även https://invented.example/report och [nere](https://down.example/a)."
    )
    assert find_unfetched_urls(report, store) == ["https://invented.example/report", "https://down.example/a"]
    assert find_unfetched_urls("Bara [en](https://authority.example/press/1).", store) == []


def test_approved_list_loads_and_rejects_lookalike_hosts():
    allowlist = Allowlist.load()
    assert allowlist.match("https://www.sakerhetspolisen.se/nyheter/") is not None
    assert allowlist.match("https://nato.int/x").country == "INT"
    assert allowlist.match("https://evilsakerhetspolisen.se/") is None
    assert allowlist.match("https://sakerhetspolisen.se.evil.example/") is None
    assert allowlist.match("https://example.org/") is None
    assert all(source.domains and source.tier in (1, 2) for source in allowlist.sources)
