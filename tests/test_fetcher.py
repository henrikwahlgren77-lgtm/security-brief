from datetime import datetime, timezone

import httpx
import pytest

from app.analysis.allowlist import Allowlist
from app.analysis.fetcher import SourceFetcher, extract_page, parse_iso_utc
from app.analysis.sources import FetchStatus, SourceError, SourceStore

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
PUBLIC_IP = "93.184.216.34"
PAGE = """<html><head><title> Säpo: lägesbild </title>
<meta property="article:published_time" content="2026-11-07T09:30:00+01:00">
<script>var secret = 1;</script></head>
<body><h1>Rubrik</h1><p>Brödtext om läget.</p><style>p{}</style></body></html>"""


class Net:
    """Counts requests and serves canned responses."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def __call__(self, request):
        self.requests.append(str(request.url))
        return self.handler(request)


def html_response(body=PAGE, status=200, content_type="text/html; charset=utf-8"):
    return httpx.Response(status, content=body.encode(), headers={"content-type": content_type})


@pytest.fixture
def make(tmp_path):
    def build(handler, resolver=lambda host: [PUBLIC_IP]):
        net = Net(handler)
        store = SourceStore(tmp_path / "s.sqlite3")
        fetcher = SourceFetcher(store, Allowlist.load(), transport=httpx.MockTransport(net),
                                resolver=resolver, clock=lambda: NOW)
        return fetcher, store, net

    return build


URL = "https://www.sakerhetspolisen.se/nyheter/lagesbild"


def test_successful_fetch_records_everything(make):
    fetcher, store, net = make(lambda r: html_response())
    record = fetcher.fetch(URL)
    assert record.fetch_status is FetchStatus.OK
    assert record.title == "Säpo: lägesbild"
    assert record.published_at == "2026-11-07T08:30:00+00:00"  # stated by the page, normalised to UTC
    assert record.fetched_at == NOW
    assert record.country == "SE" and record.region == "Norden" and record.tier == 1
    assert "Brödtext om läget." in record.content_text
    assert "secret" not in record.content_text and "p{}" not in record.content_text
    assert len(record.content_sha256) == 64
    assert store.latest_ok(URL).id == record.id


def test_publication_time_is_never_guessed(make):
    fetcher, _, _ = make(lambda r: html_response("<html><title>t</title><body>text</body></html>"))
    assert fetcher.fetch(URL).published_at is None


@pytest.mark.parametrize("url", [
    "https://example.org/page",  # not approved
    "http://www.sakerhetspolisen.se/page",  # not https
    "https://www.sakerhetspolisen.se:8443/page",  # odd port
    "https://sakerhetspolisen.se.evil.example/page",  # look-alike
])
def test_unapproved_targets_are_blocked_without_any_request(make, url):
    fetcher, store, net = make(lambda r: html_response())
    record = fetcher.fetch(url)
    assert record.fetch_status is FetchStatus.BLOCKED and record.error
    assert net.requests == []
    assert store.list_ok() == [] and len(store.list_failures()) == 1


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1"])
def test_hosts_resolving_to_private_addresses_are_blocked(make, address):
    fetcher, _, net = make(lambda r: html_response(), resolver=lambda host: [address])
    assert fetcher.fetch(URL).fetch_status is FetchStatus.BLOCKED
    assert net.requests == []


def test_redirect_within_approved_hosts_is_followed(make):
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/nyheter/ny"})
        return html_response()

    fetcher, _, net = make(handler)
    assert fetcher.fetch("https://www.sakerhetspolisen.se/old").fetch_status is FetchStatus.OK
    assert len(net.requests) == 2


def test_redirect_to_unapproved_host_is_blocked(make):
    fetcher, _, net = make(lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"}))
    record = fetcher.fetch(URL)
    assert record.fetch_status is FetchStatus.BLOCKED
    assert net.requests == [URL]


def test_redirect_loop_is_stopped(make):
    fetcher, _, net = make(lambda r: httpx.Response(302, headers={"location": URL}))
    assert fetcher.fetch(URL).fetch_status is FetchStatus.BLOCKED
    assert len(net.requests) == 4  # first request + 3 redirects


def test_http_error_is_registered(make):
    fetcher, store, _ = make(lambda r: html_response("nope", status=404))
    record = fetcher.fetch(URL)
    assert (record.fetch_status, record.http_status, record.error) == (FetchStatus.HTTP_ERROR, 404, "HTTP 404")
    assert store.fetched_urls() == set()


def test_timeout_and_dns_failure_are_registered_as_unreachable(make):
    def timeout(request):
        raise httpx.ConnectTimeout("timed out")

    fetcher, _, _ = make(timeout)
    assert fetcher.fetch(URL).fetch_status is FetchStatus.UNREACHABLE

    def no_dns(host):
        raise OSError("name not known")

    fetcher, _, _ = make(lambda r: html_response(), resolver=no_dns)
    assert fetcher.fetch(URL).fetch_status is FetchStatus.UNREACHABLE


def test_oversized_and_unsupported_content_are_rejected(make):
    fetcher, _, _ = make(lambda r: html_response("x" * 2_100_000))
    assert fetcher.fetch(URL).fetch_status is FetchStatus.ERROR
    fetcher, _, _ = make(lambda r: html_response("%PDF", content_type="application/pdf"))
    record = fetcher.fetch(URL)
    assert record.fetch_status is FetchStatus.ERROR and "content type" in record.error


def test_page_without_text_is_an_error_not_an_empty_source(make):
    fetcher, _, _ = make(lambda r: html_response("<html><script>x()</script></html>"))
    assert fetcher.fetch(URL).fetch_status is FetchStatus.ERROR


def test_malformed_url_raises_and_stores_nothing(make):
    fetcher, store, net = make(lambda r: html_response())
    with pytest.raises(SourceError):
        fetcher.fetch("not a url")
    assert net.requests == [] and store.list_failures() == []


def test_helpers():
    assert parse_iso_utc("2026-11-07T10:00:00Z") == "2026-11-07T10:00:00+00:00"
    assert parse_iso_utc("2026-11-07") == "2026-11-07T00:00:00+00:00"
    assert parse_iso_utc("yesterday") is None and parse_iso_utc(None) is None
    title, published, text = extract_page("<title>A</title><p>b</p>")
    assert (title, published, text) == ("A", None, "b")
