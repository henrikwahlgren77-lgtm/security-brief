import json
from datetime import date

import httpx
import pytest

from app.analysis import ProviderRegistry, RunStore
from app.analysis.allowlist import Allowlist
from app.analysis.fetcher import SourceFetcher
from app.analysis.job import (
    JobError,
    REQUIRED_HEADINGS,
    check_api_url,
    main,
    read_urls,
    run_job,
    submit_via_api,
    swedish_title,
)
from app.analysis.mock_provider import EMPTY_COUNTER_JSON, MockProvider, with_block
from app.analysis.sources import SourceStore

TOKEN = "ingest-token-DO-NOT-LEAK"
URL = "https://www.sakerhetspolisen.se/lagesbild"
PAGE = '<html><head><title>Säpo lägesbild</title></head><body><p>Läget är oförändrat.</p></body></html>'
ENV = {f"ANALYSIS_{r}_PROVIDER": "main" for r in ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")}


def report(extra="", sources="- [S1]"):
    body = "\n\n".join(f"{h}\n- innehåll {extra}" for h in REQUIRED_HEADINGS[:-1])
    return f"{body}\n\n## Källor\n{sources}\n"


def main_block(sources=("S1",)):
    claim = {"id": "C1", "text": "Läget är oförändrat.", "kind": "fact", "attributed_to": "Säpo",
             "classification": "confirmed", "sources": list(sources), "change": "new", "previous_refs": []}
    data = {"claims": [claim], "article5": {"category": "no_basis", "reasoning": "Inget underlag.", "sources": []},
            "hypotheses": [], "indicators": {"escalation": [], "deescalation": []}}
    return with_block("huvudanalys [S1]", json.dumps(data))


def make(tmp_path, final=None, handler=None, responses=None):
    db = tmp_path / "job.sqlite3"
    requests = []

    def route(request):
        requests.append(str(request.url))
        return handler(request) if handler else httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})

    fetcher = SourceFetcher(SourceStore(db), Allowlist.load(), transport=httpx.MockTransport(route),
                            resolver=lambda host: ["93.184.216.34"])
    provider = MockProvider(responses or ["insamlat [S1]", main_block(), with_block("granskning", EMPTY_COUNTER_JSON), final or report()])
    submitted = []

    def submit(payload):
        submitted.append(payload)
        return {"ok": True, "status": "draft"}

    return db, fetcher, provider, submitted, submit, requests


def go(tmp_path, urls=(URL,), final=None, handler=None, responses=None, submit_override=None, **kwargs):
    db, fetcher, provider, submitted, submit, requests = make(tmp_path, final, handler, responses)
    kwargs.setdefault("env", ENV)
    result = run_job(list(urls), report_date=date(2026, 11, 8), db_path=db, fetcher=fetcher,
                     registry=ProviderRegistry({"main": provider}), submit=submit_override or submit, **kwargs)
    return result, db, provider, submitted, requests


def test_successful_run_submits_one_linked_draft_with_verified_links(tmp_path):
    result, db, provider, submitted, _ = go(tmp_path)
    (payload,) = submitted
    assert result.submitted and result.sources_ok == 1 and result.sources_failed == 0
    assert payload["report_date"] == "2026-11-08" and payload["title"] == "Säkerhetsbrief – 8 november 2026"
    assert payload["analysis_run_id"] == result.run_id
    assert "[S1]" not in payload["markdown"] and f"({URL})" in payload["markdown"]  # id replaced by the fetched URL
    run = RunStore(db).get_run(result.run_id)
    assert run.status == "completed" and run.report_status == "draft" and run.source_map == {"S1": 1}
    assert "Säpo lägesbild" in provider.calls[0].context  # the model saw the fetched page, not just a URL


def test_the_title_is_fixed_by_the_job_not_chosen_by_a_model():
    assert swedish_title(date(2026, 1, 1)) == "Säkerhetsbrief – 1 januari 2026"
    assert swedish_title(date(2026, 12, 31)) == "Säkerhetsbrief – 31 december 2026"


def test_a_citation_to_an_id_that_was_never_provided_blocks_submission(tmp_path):
    with pytest.raises(JobError, match="never provided: S9") as info:
        go(tmp_path, final=report(sources="- [S1]\n- [S9]"))
    assert "(run " in str(info.value)


def test_a_url_the_model_wrote_itself_blocks_submission(tmp_path):
    submitted_calls = []
    with pytest.raises(JobError, match="never fetched.*https://invented.example/rapport"):
        go(tmp_path, final=report(extra="se https://invented.example/rapport"),
           submit_override=lambda p: submitted_calls.append(p) or {"status": "draft"})
    assert submitted_calls == []


def test_a_report_missing_a_required_section_is_not_submitted(tmp_path):
    with pytest.raises(JobError, match="missing the required section 'Redaktionell analys'"):
        go(tmp_path, final=report().replace("## Redaktionell analys", "## Något annat"))
    swapped = report().replace("## Sammanfattning", "## TEMP").replace("## Verifierade fakta", "## Sammanfattning").replace("## TEMP", "## Verifierade fakta")
    with pytest.raises(JobError, match="order"):
        go(tmp_path, final=swapped)


def test_no_fetchable_source_means_no_model_call(tmp_path):
    db, fetcher, provider, submitted, submit, _ = make(tmp_path, handler=lambda r: httpx.Response(503))
    with pytest.raises(JobError, match="none of the 1 source.*http_error.*no analysis was run"):
        run_job([URL], report_date=date(2026, 11, 8), db_path=db, fetcher=fetcher, env=ENV,
                registry=ProviderRegistry({"main": provider}), submit=submit)
    assert provider.calls == [] and submitted == []


def test_failed_sources_are_reported_to_the_model_as_gaps_not_hidden(tmp_path):
    def handler(request):
        if request.url.path == "/lagesbild":
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        return httpx.Response(404)

    result, _, provider, _, _ = go(tmp_path, urls=(URL, "https://www.sakerhetspolisen.se/saknas"), handler=handler)
    assert (result.sources_ok, result.sources_failed) == (1, 1)
    assert "Källor som inte kunde hämtas" in provider.calls[0].context and "HTTP 404" in provider.calls[0].context


def test_placeholder_models_are_refused_before_any_network_use(tmp_path):
    db, fetcher, provider, submitted, submit, requests = make(tmp_path)
    with pytest.raises(JobError, match="mock provider"):
        run_job([URL], report_date=date(2026, 11, 8), db_path=db, fetcher=fetcher, env={}, submit=submit)
    assert requests == [] and provider.calls == [] and submitted == []


def test_urls_are_validated_deduplicated_and_limited(tmp_path):
    result, _, _, _, requests = go(tmp_path, urls=(URL, URL + "?utm_source=x", URL + "/"))
    assert len(requests) == 1 and result.sources_ok == 1
    for bad in (["not a url"], ["ftp://example.org/x"], []):
        with pytest.raises(JobError):
            go(tmp_path, urls=bad)
    with pytest.raises(JobError, match="too many URLs"):
        go(tmp_path, urls=[f"https://www.sakerhetspolisen.se/{n}" for n in range(31)])


def test_without_a_submit_function_nothing_is_sent(tmp_path):
    db, fetcher, provider, submitted, submit, _ = make(tmp_path)
    result = run_job([URL], report_date=date(2026, 11, 8), db_path=db, fetcher=fetcher, env=ENV,
                     registry=ProviderRegistry({"main": provider}))
    assert result.submitted is False and "## Källor" in result.markdown and submitted == []


def test_a_reply_that_is_not_a_draft_is_an_error(tmp_path):
    with pytest.raises(JobError, match="not stored as a draft"):
        go(tmp_path, submit_override=lambda p: {"ok": True, "status": "published"})


# --- the API client -------------------------------------------------------------

def api(handler):
    return httpx.MockTransport(handler)


def test_submission_sends_the_token_as_a_header_and_expects_a_draft():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "report_date": "2026-11-08", "status": "draft"})

    submit_via_api("http://security-brief:8000", TOKEN, {"report_date": "2026-11-08", "title": "t", "markdown": "m",
                                                          "analysis_run_id": "a" * 32}, transport=api(handler))
    (request,) = seen
    assert str(request.url) == "http://security-brief:8000/api/reports"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content)["analysis_run_id"] == "a" * 32 and TOKEN.encode() not in request.content


@pytest.mark.parametrize("response, message", [
    (httpx.Response(401, json={"detail": f"bad {TOKEN}"}), "HTTP 401"),
    (httpx.Response(503, text="down"), "HTTP 503"),
    (httpx.Response(302, headers={"location": "https://evil.example"}), "HTTP 302"),
    (httpx.Response(200, text="<html>"), "not JSON"),
    (httpx.Response(200, json={"ok": True, "status": "published"}), "did not confirm"),
])
def test_api_failures_are_errors_that_never_echo_body_or_token(response, message):
    with pytest.raises(JobError, match=message) as info:
        submit_via_api("http://127.0.0.1:8098", TOKEN, {}, transport=api(lambda r: response))
    assert TOKEN not in str(info.value) and "evil.example" not in str(info.value)


def test_unreachable_api_is_a_clean_error():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect with {TOKEN}")

    with pytest.raises(JobError, match="ConnectError") as info:
        submit_via_api("http://127.0.0.1:8098", TOKEN, {}, transport=api(handler))
    assert TOKEN not in str(info.value)
    with pytest.raises(JobError, match="INGEST_TOKEN"):
        submit_via_api("http://127.0.0.1:8098", "", {})


@pytest.mark.parametrize("url", ["http://evil.example", "http://brief.kinetic.nu", "ftp://localhost", "http://user:pw@localhost",
                                 "https://brief.kinetic.nu/api", "http://localhost?x=1", ""])
def test_a_token_is_only_sent_over_https_or_inside_the_deployment(url):
    with pytest.raises(JobError):
        check_api_url(url)


@pytest.mark.parametrize("url", ["http://security-brief:8000", "http://127.0.0.1:8098", "http://localhost:8000/", "https://brief.kinetic.nu"])
def test_allowed_api_urls(url):
    assert check_api_url(url).startswith("http")


# --- command line ---------------------------------------------------------------

def test_url_files_ignore_comments_and_blank_lines(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# idag\n\nhttps://www.sakerhetspolisen.se/a\n  https://www.msb.se/b  \n#https://x.test/c\n", encoding="utf-8")
    assert read_urls(path) == ["https://www.sakerhetspolisen.se/a", "https://www.msb.se/b"]


def test_cli_without_a_token_stops_before_any_work(tmp_path, capsys):
    db, fetcher, provider, *_ = make(tmp_path)
    code = main(["--date", "2026-11-08", "--url", URL, "--db", str(db)], env=ENV,
                fetcher=fetcher, registry=ProviderRegistry({"main": provider}))
    assert code == 1 and "INGEST_TOKEN is not set" in capsys.readouterr().err and provider.calls == []


def test_cli_no_submit_prints_the_report_and_needs_no_token(tmp_path, capsys):
    db, fetcher, provider, submitted, *_ = make(tmp_path)
    code = main(["--date", "2026-11-08", "--url", URL, "--db", str(db), "--no-submit"], env=ENV,
                fetcher=fetcher, registry=ProviderRegistry({"main": provider}))
    out = capsys.readouterr().out
    assert code == 0 and "nothing submitted" in out and "## Källor" in out and submitted == []


def test_cli_submits_and_never_prints_secrets(tmp_path, capsys):
    db, fetcher, provider, submitted, submit, _ = make(tmp_path)
    env = {**ENV, "INGEST_TOKEN": TOKEN, "OPENAI_API_KEY": "sk-secret-key"}
    code = main(["--date", "2026-11-08", "--url", URL, "--db", str(db)], env=env, fetcher=fetcher, submit=submit,
                registry=ProviderRegistry({"main": provider}))
    captured = capsys.readouterr()
    assert code == 0 and len(submitted) == 1 and "draft submitted for editorial review" in captured.out
    assert TOKEN not in captured.out + captured.err and "sk-secret-key" not in captured.out + captured.err


def test_cli_reports_failures_as_exit_code_1_without_a_traceback(tmp_path, capsys):
    db, fetcher, provider, *_ = make(tmp_path, final="inga rubriker alls")
    code = main(["--date", "2026-11-08", "--url", URL, "--db", str(db), "--no-submit"], env=ENV,
                fetcher=fetcher, registry=ProviderRegistry({"main": provider}))
    err = capsys.readouterr().err
    assert code == 1 and err.startswith("error: the report is missing") and "Traceback" not in err
    assert main(["--date", "not-a-date", "--url", URL], env=ENV) == 1
    assert main(["--urls-file", str(tmp_path / "missing.txt")], env=ENV) == 1
