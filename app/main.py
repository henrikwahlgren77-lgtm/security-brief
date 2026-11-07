from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import bleach
import markdown
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from . import design_sample, pages, presentation
from .analysis.review import load_review

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Northern Europe Security Brief")
ADMIN_USER = os.getenv("APP_ADMIN_USER", "")
ADMIN_PASSWORD = os.getenv("APP_ADMIN_PASSWORD", "")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "./data/security_brief.sqlite3"))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
# Fictional design preview under /design. Off by default; never enable it on the public site.
DESIGN_PREVIEW = os.getenv("DESIGN_PREVIEW", "") == "1"

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters.update(
    long_date=presentation.long_date, short_date=presentation.short_date, month_label=presentation.month_label,
    term=presentation.term)
templates.env.globals.update(urls=pages.REAL_URLS, section=None, preview=False)
basic = HTTPBasic(auto_error=False)

ALLOWED_TAGS = [
    "p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "blockquote",
    "strong", "em", "a", "code", "pre", "hr", "table", "thead",
    "tbody", "tr", "th", "td", "br",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "title", "rel"]}


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with db() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                report_date TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                markdown TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                published_at TEXT,
                published_by TEXT,
                analysis_run_id TEXT
            )
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(reports)")}
        if "status" not in columns:
            connection.execute("ALTER TABLE reports ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        if "published_at" not in columns:
            connection.execute("ALTER TABLE reports ADD COLUMN published_at TEXT")
        if "published_by" not in columns:
            connection.execute("ALTER TABLE reports ADD COLUMN published_by TEXT")
        if "analysis_run_id" not in columns:
            connection.execute("ALTER TABLE reports ADD COLUMN analysis_run_id TEXT")


def require_admin(credentials: HTTPBasicCredentials | None = Depends(basic)) -> str:
    if not ADMIN_USER or not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin authentication is not configured")
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
            detail="Authentication required",
        )
    user_ok = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
            detail="Invalid credentials",
        )
    return credentials.username


def require_ingest_token(authorization: str | None = Header(default=None)) -> None:
    if not INGEST_TOKEN:
        raise HTTPException(status_code=503, detail="Ingestion is not configured")
    expected = f"Bearer {INGEST_TOKEN}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid ingestion token")


class ReportIn(BaseModel):
    report_date: date
    title: str = Field(min_length=1, max_length=200)
    markdown: str = Field(min_length=1, max_length=200_000)
    # Set by the analysis job: links the draft to the run log shown on the review page.
    analysis_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip()


def rendered(markdown_text: str) -> str:
    raw_html = markdown.markdown(
        markdown_text,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html5",
    )
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def get_reports() -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute(
            "SELECT report_date, title, markdown, created_at, updated_at, published_at "
            "FROM reports WHERE status = 'published' ORDER BY report_date DESC"
        ).fetchall()


def get_report(report_date: str) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute(
            "SELECT * FROM reports WHERE report_date = ? AND status = 'published'", (report_date,)
        ).fetchone()


def get_editorial_reports() -> list[sqlite3.Row]:
    with db() as connection:
        return connection.execute(
            "SELECT report_date, title, status, created_at, updated_at, analysis_run_id "
            "FROM reports ORDER BY report_date DESC"
        ).fetchall()


def publish_token(report_date: str) -> str:
    return hmac.new(ADMIN_PASSWORD.encode(), f"publish:{report_date}".encode(), hashlib.sha256).hexdigest()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; img-src 'self' data:; "
        "script-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    context = pages.home_context(get_reports(), rendered, pages.REAL_URLS)
    return templates.TemplateResponse(request=request, name="index.html", context={"app_name": APP_NAME, **context})


@app.get("/arkiv", response_class=HTMLResponse)
def archive(request: Request, manad: str | None = None, amne: str | None = None, q: str | None = None):
    context = pages.archive_context(get_reports(), pages.REAL_URLS, manad, amne, q)
    return templates.TemplateResponse(request=request, name="archive.html", context={"app_name": APP_NAME, **context})


@app.get("/metod", response_class=HTMLResponse)
def method(request: Request):
    return templates.TemplateResponse(
        request=request, name="method.html", context={"app_name": APP_NAME, "urls": pages.REAL_URLS, "section": "method"})


@app.get("/reports/{report_date}", response_class=HTMLResponse)
def report_page(
    request: Request,
    report_date: str,
):
    try:
        date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    report = get_report(report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    context = pages.report_context(report, get_reports(), rendered, pages.REAL_URLS)
    return templates.TemplateResponse(request=request, name="report.html", context={"app_name": APP_NAME, **context})


@app.post("/api/reports", dependencies=[Depends(require_ingest_token)])
def upsert_report(payload: ReportIn):
    now = datetime.now(timezone.utc).isoformat()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO reports (report_date, title, markdown, created_at, updated_at, status, published_at, published_by, analysis_run_id)
            VALUES (?, ?, ?, ?, ?, 'draft', NULL, NULL, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                title=excluded.title,
                markdown=excluded.markdown,
                updated_at=excluded.updated_at,
                status='draft',
                published_at=NULL,
                published_by=NULL,
                analysis_run_id=excluded.analysis_run_id
            """,
            (payload.report_date.isoformat(), payload.title, payload.markdown, now, now, payload.analysis_run_id),
        )
    return {"ok": True, "report_date": payload.report_date.isoformat(), "status": "draft"}


@app.get("/admin")
def admin(request: Request, user: str = Depends(require_admin)):
    reports = get_editorial_reports()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"app_name": APP_NAME, "reports": reports, "tokens": {
            row["report_date"]: publish_token(row["report_date"]) for row in reports
        }},
    )


@app.post("/admin/reports/{report_date}/publish")
def publish_report(
    report_date: str,
    csrf_token: str = Form(),
    ack_review: str | None = Form(default=None),
    user: str = Depends(require_admin),
):
    try:
        date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    if not hmac.compare_digest(csrf_token, publish_token(report_date)):
        raise HTTPException(status_code=403, detail="Invalid publishing token")
    with db() as connection:
        linked = connection.execute(
            "SELECT analysis_run_id FROM reports WHERE report_date = ? AND status = 'draft'", (report_date,)
        ).fetchone()
    # A draft produced by the analysis job needs the editor's explicit confirmation that the
    # review material (warnings, unresolved objections, sources) was read. Nothing else unlocks it.
    if linked is not None and linked["analysis_run_id"] and ack_review != "yes":
        raise HTTPException(status_code=400, detail="Confirm that the analysis review has been read before publishing")
    now = datetime.now(timezone.utc).isoformat()
    with db() as connection:
        result = connection.execute(
            "UPDATE reports SET status='published', published_at=?, published_by=?, updated_at=? "
            "WHERE report_date=? AND status='draft'",
            (now, user, now, report_date),
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Draft report not found")
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/reports/{report_date}/review", response_class=HTMLResponse)
def review_report(request: Request, report_date: str, user: str = Depends(require_admin)):
    try:
        date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    with db() as connection:
        report = connection.execute("SELECT * FROM reports WHERE report_date = ?", (report_date,)).fetchone()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "app_name": APP_NAME,
            "report": report,
            "report_html": rendered(report["markdown"]),
            "review": load_review(DATABASE_PATH, report["analysis_run_id"]),
            "linked": bool(report["analysis_run_id"]),
            "token": publish_token(report_date),
        },
    )


# --- Design preview: the same templates with fictional rows. Disabled unless DESIGN_PREVIEW=1. ---

def _preview_only() -> None:
    if not DESIGN_PREVIEW:
        raise HTTPException(status_code=404, detail="Not found")


def _preview(request: Request, name: str, context: dict) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name=name, context={"app_name": APP_NAME, "preview": True, **context})


@app.get("/design", include_in_schema=False)
def design_redirect():
    _preview_only()
    return RedirectResponse(url="/design/", status_code=307)


@app.get("/design/", response_class=HTMLResponse, include_in_schema=False)
def design_home(request: Request):
    _preview_only()
    extras = {"headline": design_sample.HEADLINE, "standfirst": design_sample.STANDFIRST, "developments": design_sample.DEVELOPMENTS,
              "regions": design_sample.REGIONS, "changes": design_sample.CHANGES}
    return _preview(request, "index.html", pages.home_context(design_sample.ROWS, rendered, pages.PREVIEW_URLS, extras))


@app.get("/design/rapport/{report_date}", response_class=HTMLResponse, include_in_schema=False)
def design_report(request: Request, report_date: str):
    _preview_only()
    row = next((r for r in design_sample.ROWS if r["report_date"] == report_date), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    extras = {"headline": design_sample.HEADLINE, "standfirst": design_sample.STANDFIRST} if row is design_sample.LATEST else {}
    return _preview(request, "report.html", pages.report_context(row, design_sample.ROWS, rendered, pages.PREVIEW_URLS, extras))


@app.get("/design/arkiv", response_class=HTMLResponse, include_in_schema=False)
def design_archive(request: Request, manad: str | None = None, amne: str | None = None, q: str | None = None):
    _preview_only()
    return _preview(request, "archive.html", pages.archive_context(design_sample.ROWS, pages.PREVIEW_URLS, manad, amne, q))


@app.get("/design/metod", response_class=HTMLResponse, include_in_schema=False)
def design_method(request: Request):
    _preview_only()
    return _preview(request, "method.html", {"urls": pages.PREVIEW_URLS, "section": "method"})


@app.get("/design/typografi", response_class=HTMLResponse, include_in_schema=False)
def design_typography(request: Request):
    _preview_only()
    return _preview(request, "typography.html", pages.typography_context(pages.PREVIEW_URLS))
