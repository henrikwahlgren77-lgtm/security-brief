import sqlite3
from pathlib import Path
from app.config import settings

def connect():
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content_markdown TEXT NOT NULL,
            content_html TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return db

def save_report(report_date: str, title: str, markdown: str, html: str):
    with connect() as db:
        db.execute("""
            INSERT INTO reports (report_date, title, content_markdown, content_html)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                title=excluded.title,
                content_markdown=excluded.content_markdown,
                content_html=excluded.content_html,
                updated_at=CURRENT_TIMESTAMP
        """, (report_date, title, markdown, html))

def list_reports(limit: int = 100):
    with connect() as db:
        return db.execute(
            "SELECT report_date, title, created_at, updated_at FROM reports ORDER BY report_date DESC LIMIT ?",
            (limit,)
        ).fetchall()

def get_report(report_date: str):
    with connect() as db:
        return db.execute(
            "SELECT * FROM reports WHERE report_date = ?", (report_date,)
        ).fetchone()
