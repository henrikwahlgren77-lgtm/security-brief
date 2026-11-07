from fastapi.testclient import TestClient
from app.main import app, rendered

client = TestClient(app)

def test_health_is_public():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_markdown_renderer_removes_script_tags():
    html = rendered("Safe text\n\n<script>alert('x')</script>")
    assert "Safe text" in html
    assert "<script>" not in html
    assert "</script>" not in html

def test_markdown_renderer_keeps_https_links():
    html = rendered("[Official source](https://example.com/report)")
    assert 'href="https://example.com/report"' in html
