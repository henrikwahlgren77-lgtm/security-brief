from __future__ import annotations

import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import bleach
import markdown
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Northern Europe Security Brief")
ADMIN_USER = os.getenv("APP_ADMIN_USER", "")
ADMIN_PASSWORD = os.getenv("APP_ADMIN_PASSWORD", "")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
PUBLIC_REPORTS = os.getenv("PUBLIC_REPORTS", "false").lower() == "true"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "./data/security_brief.sqlite3"))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
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
                updated_at TEXT NOT NULL
            )
        """)


@app.on_event("startup")
def startup() -> None:
    init_db()


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
            "SELECT report_date, title, created_at, updated_at "
            "FROM reports ORDER BY report_date DESC"
        ).fetchall()


def get_report(report_date: str) -> sqlite3.Row | None:
    with db() as connection:
        return connection.execute(
            "SELECT * FROM reports WHERE report_date = ?", (report_date,)
        ).fetchone()


def archive_access(user: str | None) -> None:
    if not PUBLIC_REPORTS and user is None:
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Basic"},
            detail="Authentication required",
        )


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
def home(request: Request, user: str | None = Depends(require_admin_optional)):
    archive_access(user)
    reports = get_reports()
    latest = get_report(reports[0]["report_date"]) if reports else None
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": APP_NAME,
            "reports": reports,
            "latest": latest,
            "latest_html": rendered(latest["markdown"]) if latest else "",
        },
    )


def require_admin_optional(
    credentials: HTTPBasicCredentials | None = Depends(basic),
) -> str | None:
    if PUBLIC_REPORTS and credentials is None:
        return None
    if credentials is None:
        return None
    return require_admin(credentials)


@app.get("/reports/{report_date}", response_class=HTMLResponse)
def report_page(
    request: Request,
    report_date: str,
    user: str | None = Depends(require_admin_optional),
):
    archive_access(user)
    try:
        date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    report = get_report(report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={
            "app_name": APP_NAME,
            "report": report,
            "report_html": rendered(report["markdown"]),
        },
    )


@app.post("/api/reports", dependencies=[Depends(require_ingest_token)])
def upsert_report(payload: ReportIn):
    now = datetime.now(timezone.utc).isoformat()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO reports (report_date, title, markdown, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                title=excluded.title,
                markdown=excluded.markdown,
                updated_at=excluded.updated_at
            """,
            (payload.report_date.isoformat(), payload.title, payload.markdown, now, now),
        )
    return {"ok": True, "report_date": payload.report_date.isoformat()}


@app.get("/admin")
def admin(user: str = Depends(require_admin)):
    return RedirectResponse(url="/", status_code=303)
