"""Read-only view of an analysis run for the editorial review page.

The web app uses this to show an editor what stands behind a draft. It opens
the database read-only and never creates tables, so the web container cannot
change analysis data through it, and a database without analysis tables is
simply "no data".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .runs import RunRecord, RunStore


@dataclass(frozen=True, slots=True)
class SourceView:
    id: str  # S1, S2 ... as cited in the report
    name: str
    title: str | None
    url: str | None  # only http(s); anything else is shown as text
    tier: int | None
    published_at: str | None
    fetched_at: str


@dataclass(frozen=True, slots=True)
class ReviewData:
    run: RunRecord
    sources: tuple[SourceView, ...]

    @property
    def assessment(self) -> dict:
        return self.run.assessment or {}

    @property
    def added_objections(self) -> set[str]:
        return set(self.assessment.get("added_objections", []))

    @property
    def objections(self) -> list[dict]:
        return list(self.assessment.get("objections", []))

    @property
    def unresolved_objections(self) -> list[dict]:
        return [o for o in self.objections if o.get("severity") in ("decisive", "substantial")]

    @property
    def issues(self) -> list[dict]:
        issues = list(self.assessment.get("issues", []))
        return sorted(issues, key=lambda i: i.get("level") != "warn")  # warnings first

    @property
    def claims(self) -> list[dict]:
        return list(self.assessment.get("claims", []))


def load_review(database_path: Path | str, run_id: str | None) -> ReviewData | None:
    """Return the run and its sources, or None if there is nothing to show."""
    if not run_id:
        return None
    try:
        connection = sqlite3.connect(f"file:{Path(database_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        stages = connection.execute(
            "SELECT * FROM analysis_stages WHERE run_id = ? ORDER BY position, id", (run_id,)
        ).fetchall()
        run = RunStore._run_from_rows(row, stages)
        views = []
        for source_id, row_id in sorted(run.source_map.items(), key=lambda kv: int(kv[0][1:])):
            found = connection.execute(
                "SELECT name, title, url, tier, published_at, fetched_at FROM sources WHERE id = ?", (row_id,)
            ).fetchone()
            if found is None:
                continue
            url = found["url"] if str(found["url"]).startswith(("https://", "http://")) else None
            views.append(SourceView(source_id, found["name"], found["title"], url, found["tier"],
                                    found["published_at"], found["fetched_at"]))
        return ReviewData(run=run, sources=tuple(views))
    except sqlite3.OperationalError:  # analysis tables do not exist in this database
        return None
    finally:
        connection.close()
