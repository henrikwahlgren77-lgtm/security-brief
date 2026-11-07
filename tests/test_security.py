import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(main, "ADMIN_USER", "editor")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "a-long-test-password")
    monkeypatch.setattr(main, "INGEST_TOKEN", "test-ingest-token")
    with TestClient(main.app) as test_client:
        yield test_client


def submit_report(client, report_date="2026-11-07", title="Test report", markdown="## Verified\n\nPublic source: [report](https://example.com/source)"):
    return client.post(
        "/api/reports",
        headers={"Authorization": "Bearer test-ingest-token"},
        json={"report_date": report_date, "title": title, "markdown": markdown},
    )


def publish(client, report_date="2026-11-07", auth=("editor", "a-long-test-password")):
    return client.post(
        f"/admin/reports/{report_date}/publish",
        data={"csrf_token": main.publish_token(report_date)},
        auth=auth,
        follow_redirects=False,
    )


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_reports_are_drafts_and_public_routes_only_show_published(client):
    assert submit_report(client).json()["status"] == "draft"
    home = client.get("/")
    assert home.status_code == 200
    assert "Test report" not in home.text
    assert client.get("/reports/2026-11-07").status_code == 404
    assert "Test report" in client.get("/admin", auth=("editor", "a-long-test-password")).text


def test_admin_routes_require_authentication(client):
    assert client.get("/admin").status_code == 401
    assert client.post("/admin/reports/2026-11-07/publish", data={"csrf_token": "bad"}).status_code == 401
    assert client.post("/api/reports", json={"report_date": "2026-11-07", "title": "X", "markdown": "Y"}).status_code == 401


def test_only_authenticated_editor_can_publish_a_draft(client):
    submit_report(client)
    assert publish(client, auth=("editor", "wrong-password")).status_code == 401
    assert client.post(
        "/admin/reports/2026-11-07/publish",
        data={"csrf_token": "invalid"},
        auth=("editor", "a-long-test-password"),
    ).status_code == 403

    response = publish(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "Test report" in client.get("/").text
    assert client.get("/reports/2026-11-07").status_code == 200


def test_ingest_update_returns_published_report_to_draft(client):
    submit_report(client)
    assert publish(client).status_code == 303
    updated = submit_report(client, title="Updated report")
    assert updated.json()["status"] == "draft"
    assert "Updated report" not in client.get("/").text
    assert client.get("/reports/2026-11-07").status_code == 404


def test_existing_reports_migrate_to_drafts(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE reports (
                report_date TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                markdown TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO reports VALUES (?, ?, ?, ?, ?)",
            ("2026-11-06", "Old report", "Old text", "now", "now"),
        )
    monkeypatch.setattr(main, "DATABASE_PATH", path)

    main.init_db()

    with sqlite3.connect(path) as connection:
        status_value = connection.execute(
            "SELECT status FROM reports WHERE report_date='2026-11-06'"
        ).fetchone()[0]
    assert status_value == "draft"


def test_markdown_renderer_removes_unsafe_html_and_links():
    html = main.rendered(
        "Safe text\n\n<script>alert('x')</script>"
        "\n\n[bad](javascript:alert%281%29)"
        "\n\n<img src=x onerror=alert(1)>"
    )
    assert "Safe text" in html
    assert "<script>" not in html and "</script>" not in html
    assert "javascript:" not in html
    assert "onerror" not in html


def test_markdown_renderer_keeps_https_links():
    html = main.rendered("[Official source](https://example.com/report)")
    assert 'href="https://example.com/report"' in html
