import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.analysis import AnalysisConfig, AnalysisPipeline, AnalysisRole, ProviderRegistry, RoleConfig, RunStore
from app.analysis.context import format_sources_for_prompt
from app.analysis.mock_provider import EMPTY_COUNTER_JSON, MockProvider, with_block
from app.analysis.sources import FetchStatus, OriginType, SourceRecord, SourceStore

EDITOR = ("editor", "a-long-test-password")
XSS = "<script>alert('x')</script>"
IMG = "<img src=x onerror=alert(1)>"
MISSING_RUN = "f" * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(main, "ADMIN_USER", "editor")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "a-long-test-password")
    monkeypatch.setattr(main, "INGEST_TOKEN", "test-ingest-token")
    with TestClient(main.app) as test_client:
        yield test_client


def make_run(db_path):
    """A real pipeline run whose model text and source names contain hostile HTML."""
    sources = SourceStore(db_path)
    record = sources.add(SourceRecord(
        name=f"Nyhetsbyrå {XSS}", title=f"Rubrik {IMG}", url="https://news.example.test/a", fetched_at=datetime(2026, 11, 8, 6, tzinfo=timezone.utc),
        fetch_status=FetchStatus.OK, content_sha256="a" * 64, content_text="text", tier=None, origin_type=OriginType.ORIGINAL))
    text, id_map = format_sources_for_prompt([record])
    claim = {"id": "C1", "text": f"Något hände {IMG}", "kind": "fact", "attributed_to": None, "classification": "confirmed",
             "sources": ["S1"], "change": "new", "previous_refs": []}
    main_json = {"claims": [claim], "article5": {"category": "no_basis", "reasoning": "Inget underlag.", "sources": []},
                 "hypotheses": [{"event": "Händelse", "strongest": 1, "explanations": [{"text": "A"}, {"text": "B", "against": [XSS]}]}],
                 "indicators": {"escalation": [], "deescalation": []}}
    counter = {"objections": [{"id": "O1", "target": "C1", "type": "unsupported", "severity": "decisive", "text": f"Stöd saknas <b>helt</b>"}],
               "section_verdicts": [], "disagreements": []}
    final = "## Sammanfattning\n- Punkt.\n\n## Osäkerheter och luckor\n- Inget.\n\n## Källor\n- [S1]\n"
    provider = MockProvider(["insamlat", with_block("analys", json.dumps(main_json)), with_block("granskning", json.dumps(counter)), final])
    config = AnalysisConfig(roles={role: RoleConfig(provider="main") for role in AnalysisRole})
    draft = AnalysisPipeline(config, ProviderRegistry({"main": provider}), run_store=RunStore(db_path)).analyze(text, source_map=id_map)
    return draft


def submit(client, run_id=None, report_date="2026-11-07", title="Analysutkast", markdown="## Sammanfattning\n\nText"):
    payload = {"report_date": report_date, "title": title, "markdown": markdown}
    if run_id is not None:
        payload["analysis_run_id"] = run_id
    return client.post("/api/reports", headers={"Authorization": "Bearer test-ingest-token"}, json=payload)


def publish(client, ack=None, report_date="2026-11-07", token=None):
    data = {"csrf_token": token or main.publish_token(report_date)}
    if ack is not None:
        data["ack_review"] = ack
    return client.post(f"/admin/reports/{report_date}/publish", data=data, auth=EDITOR, follow_redirects=False)


def row(client, report_date="2026-11-07"):
    with sqlite3.connect(main.DATABASE_PATH) as connection:
        return connection.execute("SELECT status, analysis_run_id FROM reports WHERE report_date = ?", (report_date,)).fetchone()


def linked_draft(client):
    draft = make_run(main.DATABASE_PATH)
    assert submit(client, draft.run_id).status_code == 200
    return draft


def test_ingest_stores_the_run_link_and_rejects_malformed_ids(client):
    assert submit(client, "a" * 32).json()["status"] == "draft"
    assert row(client) == ("draft", "a" * 32)
    for bad in ("short", "A" * 32, "g" * 32, "../../etc/passwd", "a" * 33):
        assert submit(client, bad, report_date="2026-11-08").status_code == 422


def test_resubmitting_without_a_run_id_clears_the_link(client):
    submit(client, "a" * 32)
    submit(client, None, markdown="## Sammanfattning\n\nManuellt omskriven")
    assert row(client) == ("draft", None)


def test_an_older_database_gains_the_column(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE reports (report_date TEXT PRIMARY KEY, title TEXT NOT NULL, markdown TEXT NOT NULL, "
                           "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', "
                           "published_at TEXT, published_by TEXT)")
    monkeypatch.setattr(main, "DATABASE_PATH", path)
    main.init_db()
    with sqlite3.connect(path) as connection:
        assert "analysis_run_id" in {r[1] for r in connection.execute("PRAGMA table_info(reports)")}


def test_the_queue_sends_analysis_drafts_to_the_review_page_not_straight_to_publishing(client):
    linked_draft(client)
    submit(client, None, report_date="2026-11-06", title="Manuellt utkast")
    page = client.get("/admin", auth=EDITOR).text
    assert "/admin/reports/2026-11-07/review" in page and 'action="/admin/reports/2026-11-07/publish"' not in page
    assert 'action="/admin/reports/2026-11-06/publish"' in page  # reports without analysis behave as before


def test_the_review_page_requires_the_editor_login_and_a_real_report(client):
    linked_draft(client)
    assert client.get("/admin/reports/2026-11-07/review").status_code == 401
    assert client.get("/admin/reports/2026-11-07/review", auth=("editor", "wrong")).status_code == 401
    assert client.get("/admin/reports/2026-11-09/review", auth=EDITOR).status_code == 404
    assert client.get("/admin/reports/not-a-date/review", auth=EDITOR).status_code == 404


def test_the_review_page_shows_what_an_editor_needs_to_decide(client):
    linked_draft(client)
    page = client.get("/admin/reports/2026-11-07/review", auth=EDITOR).text
    assert "Olösta invändningar" in page and "O1" in page and "Avgörande" in page and "Källstöd saknas" in page
    assert "tillagd av systemet" in page                              # the writer omitted it, the system added it
    assert "nedgraderad från bekräftad" in page and "Trovärdig men obekräftad" in page                        # one non-primary source cannot confirm
    assert "insufficient_verification" in page                         # warning surfaced
    assert "Artikel 5-kategori" in page and "Inget underlag" in page
    assert 'href="https://news.example.test/a"' in page and "Nyhetsbyrå" in page
    assert "main_analysis" in page and "main / –" in page             # model step table: role, provider / model
    assert 'name="ack_review"' in page and "required" in page


def test_model_and_source_text_cannot_inject_html_into_the_review_page(client):
    linked_draft(client)
    page = client.get("/admin/reports/2026-11-07/review", auth=EDITOR).text
    for raw in (XSS, IMG, "<b>helt</b>"):
        assert raw not in page
    assert "&lt;script&gt;" in page and "&lt;img src=x" in page and "&lt;b&gt;helt&lt;/b&gt;" in page


def test_the_review_page_is_read_only_and_copes_with_missing_analysis_data(client):
    submit(client, MISSING_RUN)  # run id with no analysis tables at all in this database
    response = client.get("/admin/reports/2026-11-07/review", auth=EDITOR)
    assert response.status_code == 200 and "kunde inte läsas" in response.text and 'name="ack_review"' in response.text
    with sqlite3.connect(main.DATABASE_PATH) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "analysis_runs" not in tables  # viewing never creates or alters analysis data


def test_a_report_without_analysis_says_so_and_needs_no_confirmation(client):
    submit(client, None)
    page = client.get("/admin/reports/2026-11-07/review", auth=EDITOR).text
    assert "inget analysunderlag" in page and 'name="ack_review"' not in page
    assert publish(client).status_code == 303


@pytest.mark.parametrize("ack", [None, "no", "yes ", "on", ""])
def test_publishing_an_analysis_draft_needs_the_explicit_confirmation(client, ack):
    linked_draft(client)
    assert publish(client, ack=ack).status_code == 400
    assert row(client)[0] == "draft" and client.get("/reports/2026-11-07").status_code == 404


def test_confirmation_unlocks_publishing_but_not_without_the_csrf_token_or_login(client):
    linked_draft(client)
    assert publish(client, ack="yes", token="invalid").status_code == 403
    assert client.post("/admin/reports/2026-11-07/publish", data={"csrf_token": main.publish_token("2026-11-07"), "ack_review": "yes"}).status_code == 401
    assert row(client)[0] == "draft"
    assert publish(client, ack="yes").status_code == 303
    assert row(client)[0] == "published" and client.get("/reports/2026-11-07").status_code == 200
    assert "publicerad" in client.get("/admin/reports/2026-11-07/review", auth=EDITOR).text


def test_a_new_submission_after_publishing_needs_the_review_again(client):
    draft = linked_draft(client)
    assert publish(client, ack="yes").status_code == 303
    submit(client, draft.run_id, markdown="## Sammanfattning\n\nÄndrad text")
    assert row(client)[0] == "draft"
    assert publish(client, ack=None).status_code == 400


def test_reading_analysis_data_can_never_create_or_change_a_database(tmp_path):
    from app.analysis.review import load_review

    missing = tmp_path / "does-not-exist.sqlite3"
    assert load_review(missing, "a" * 32) is None and not missing.exists()
    assert load_review(missing, None) is None
    draft = make_run(tmp_path / "real.sqlite3")
    before = (tmp_path / "real.sqlite3").read_bytes()
    review = load_review(tmp_path / "real.sqlite3", draft.run_id)
    assert review.run.id == draft.run_id and [s.id for s in review.sources] == ["S1"]
    assert (tmp_path / "real.sqlite3").read_bytes() == before
    assert load_review(tmp_path / "real.sqlite3", "b" * 32) is None
