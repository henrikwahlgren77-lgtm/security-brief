"""Run log: what was asked, of which model, with which prompt, and what came back.

Every analysis run and every stage in it is stored, including failed ones, so
a draft can be traced and its inputs recovered. Nothing here stores API keys:
the configuration snapshot holds provider, model, token limit, prompt version
and operator-supplied prices only.

Stored text (source material, model output, the draft) is as sensitive as the
sources themselves; keep the database on the analysis host.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

from .providers import AnalysisConfig, ProviderResponse

logger = logging.getLogger("app.analysis.runs")

REPORT_STATUS = "draft"  # the run log can only ever hold a draft
MAX_ERROR_CHARS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class StageRecord:
    role: str
    position: int
    status: str  # ok | failed
    provider: str
    requested_model: str | None
    served_model: str | None
    prompt_version: str | None
    prompt_sha256: str | None
    input_text: str
    output_text: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    latency_ms: int | None
    attempts: int | None
    finish_reason: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    status: str  # running | completed | failed
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    config: dict
    source_map: dict[str, int]  # "S1" -> sources.id
    source_material_sha256: str
    report: str | None
    report_status: str
    warnings: tuple[str, ...]
    stages: tuple[StageRecord, ...] = field(default_factory=tuple)

    @property
    def input_tokens(self) -> int | None:
        return _sum([s.input_tokens for s in self.stages])

    @property
    def output_tokens(self) -> int | None:
        return _sum([s.output_tokens for s in self.stages])

    @property
    def known_cost(self) -> float:
        return sum(s.estimated_cost for s in self.stages if s.estimated_cost is not None)

    @property
    def cost_complete(self) -> bool:
        """True only if every stage has a cost. Otherwise ``known_cost`` is a lower bound."""
        return bool(self.stages) and all(s.estimated_cost is not None for s in self.stages)

    @property
    def duration_seconds(self) -> float | None:
        return (self.finished_at - self.started_at).total_seconds() if self.finished_at else None


def _sum(values: Sequence[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def config_snapshot(config: AnalysisConfig) -> dict:
    return {
        role.value: {
            "provider": rc.provider,
            "model": rc.model,
            "max_output_tokens": rc.max_output_tokens,
            "prompt_version": rc.prompt_version,
            "price_input_per_mtok": rc.price_input_per_mtok,
            "price_output_per_mtok": rc.price_output_per_mtok,
        }
        for role, rc in config.roles.items()
    }


class RunStore:
    def __init__(self, path: Path | str, clock: Callable[[], datetime] = _now):
        self.path = str(path)
        self.clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT,
                    config TEXT NOT NULL,
                    source_map TEXT NOT NULL,
                    source_material_sha256 TEXT NOT NULL,
                    report TEXT,
                    report_status TEXT NOT NULL DEFAULT 'draft',
                    warnings TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_stages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES analysis_runs(id),
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    requested_model TEXT,
                    served_model TEXT,
                    prompt_version TEXT,
                    prompt_sha256 TEXT,
                    input_text TEXT NOT NULL,
                    output_text TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    estimated_cost REAL,
                    latency_ms INTEGER,
                    attempts INTEGER,
                    finish_reason TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS stages_run ON analysis_stages (run_id, position)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # -- writing -----------------------------------------------------------
    def start_run(
        self, config: AnalysisConfig, source_material: str, source_map: Mapping[str, int] | None = None
    ) -> str:
        run_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO analysis_runs (id, status, started_at, config, source_map, source_material_sha256) "
                "VALUES (?, 'running', ?, ?, ?, ?)",
                (
                    run_id,
                    self.clock().isoformat(),
                    json.dumps(config_snapshot(config), sort_keys=True),
                    json.dumps(dict(source_map or {}), sort_keys=True),
                    hashlib.sha256(source_material.encode("utf-8")).hexdigest(),
                ),
            )
        logger.info("run %s started", run_id)
        return run_id

    def record_stage(
        self,
        run_id: str,
        *,
        position: int,
        role: str,
        provider: str,
        requested_model: str | None,
        prompt_version: str | None,
        prompt_sha256: str | None,
        input_text: str,
        started_at: datetime,
        response: ProviderResponse | None = None,
        estimated_cost: float | None = None,
        error: str | None = None,
    ) -> None:
        if (response is None) == (error is None):
            raise ValueError("A stage records either a response or an error")
        finished_at = self.clock()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_stages (run_id, position, role, status, provider, requested_model,
                    served_model, prompt_version, prompt_sha256, input_text, output_text, input_tokens,
                    output_tokens, estimated_cost, latency_ms, attempts, finish_reason, error,
                    started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, position, role, "ok" if response else "failed", provider, requested_model,
                    response.model if response else None, prompt_version, prompt_sha256, input_text,
                    response.text if response else None,
                    response.input_tokens if response else None,
                    response.output_tokens if response else None,
                    estimated_cost,
                    response.latency_ms if response else None,
                    response.attempts if response else None,
                    response.finish_reason if response else None,
                    error[:MAX_ERROR_CHARS] if error else None,
                    started_at.isoformat(), finished_at.isoformat(),
                ),
            )
        # Metadata only: never content, never credentials.
        logger.info(
            "run %s stage %s %s provider=%s model=%s prompt=%s tokens_in=%s tokens_out=%s latency_ms=%s attempts=%s%s",
            run_id, role, "ok" if response else "FAILED", provider,
            (response.model if response else None) or requested_model, prompt_version,
            response.input_tokens if response else None, response.output_tokens if response else None,
            response.latency_ms if response else None, response.attempts if response else None,
            f" error={error[:MAX_ERROR_CHARS]}" if error else "",
        )

    def finish_run(
        self, run_id: str, *, report: str | None = None, warnings: Sequence[str] = (), error: str | None = None
    ) -> None:
        if (report is None) == (error is None):
            raise ValueError("A run finishes with either a report or an error")
        with self._connect() as connection:
            connection.execute(
                "UPDATE analysis_runs SET status = ?, finished_at = ?, error = ?, report = ?, "
                "report_status = ?, warnings = ? WHERE id = ? AND status = 'running'",
                (
                    "completed" if report is not None else "failed", self.clock().isoformat(),
                    error[:MAX_ERROR_CHARS] if error else None, report, REPORT_STATUS,
                    json.dumps(list(warnings)), run_id,
                ),
            )
        logger.info("run %s %s", run_id, "completed" if report is not None else f"FAILED: {error[:MAX_ERROR_CHARS]}")

    # -- reading -----------------------------------------------------------
    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            stages = connection.execute(
                "SELECT * FROM analysis_stages WHERE run_id = ? ORDER BY position, id", (run_id,)
            ).fetchall()
        return self._run_from_rows(row, stages)

    def list_runs(self, limit: int = 20) -> list[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM analysis_runs ORDER BY started_at DESC, id LIMIT ?", (max(1, limit),)
            ).fetchall()
            result = []
            for row in rows:
                stages = connection.execute(
                    "SELECT * FROM analysis_stages WHERE run_id = ? ORDER BY position, id", (row["id"],)
                ).fetchall()
                result.append(self._run_from_rows(row, stages))
        return result

    def usage_by_model(self, since: datetime | None = None) -> list[dict]:
        """Token use per provider and served model. Cost is a sum of known costs only."""
        query = (
            "SELECT provider, COALESCE(served_model, requested_model) AS model, COUNT(*) AS calls, "
            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(estimated_cost) AS known_cost, SUM(estimated_cost IS NULL) AS calls_without_cost, "
            "SUM(status = 'failed') AS failed_calls FROM analysis_stages"
        )
        params: tuple = ()
        if since is not None:
            if since.tzinfo is None:
                raise ValueError("since must be timezone-aware")
            query += " WHERE started_at >= ?"
            params = (since.astimezone(timezone.utc).isoformat(),)
        query += " GROUP BY provider, model ORDER BY provider, model"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    @staticmethod
    def _run_from_rows(row: sqlite3.Row, stages: list[sqlite3.Row]) -> RunRecord:
        return RunRecord(
            id=row["id"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            error=row["error"],
            config=json.loads(row["config"]),
            source_map=json.loads(row["source_map"]),
            source_material_sha256=row["source_material_sha256"],
            report=row["report"],
            report_status=row["report_status"],
            warnings=tuple(json.loads(row["warnings"])),
            stages=tuple(
                StageRecord(
                    role=s["role"], position=s["position"], status=s["status"], provider=s["provider"],
                    requested_model=s["requested_model"], served_model=s["served_model"],
                    prompt_version=s["prompt_version"], prompt_sha256=s["prompt_sha256"],
                    input_text=s["input_text"], output_text=s["output_text"],
                    input_tokens=s["input_tokens"], output_tokens=s["output_tokens"],
                    estimated_cost=s["estimated_cost"], latency_ms=s["latency_ms"], attempts=s["attempts"],
                    finish_reason=s["finish_reason"], error=s["error"],
                    started_at=datetime.fromisoformat(s["started_at"]),
                    finished_at=datetime.fromisoformat(s["finished_at"]),
                )
                for s in stages
            ),
        )


def _format_run(run: RunRecord) -> str:
    cost = f"{run.known_cost:.4f}" + ("" if run.cost_complete else " (lower bound: some stages have no price)")
    lines = [
        f"{run.id}  {run.status}  started {run.started_at.isoformat()}  "
        f"tokens in/out {run.input_tokens}/{run.output_tokens}  cost {cost}",
    ]
    if run.error:
        lines.append(f"  error: {run.error}")
    for warning in run.warnings:
        lines.append(f"  warning: {warning}")
    for s in run.stages:
        lines.append(
            f"  {s.position}. {s.role:<16} {s.status:<6} {s.provider}/{s.served_model or s.requested_model} "
            f"prompt {s.prompt_version} tokens {s.input_tokens}/{s.output_tokens} {s.latency_ms}ms attempts {s.attempts}"
            + (f"  error: {s.error}" if s.error else "")
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print run metadata (never source text or model output): ``python -m app.analysis.runs list|show``."""
    import argparse
    import os

    parser = argparse.ArgumentParser(prog="python -m app.analysis.runs", description=main.__doc__)
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "./data/security_brief.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").add_argument("--limit", type=int, default=10)
    sub.add_parser("show").add_argument("run_id")
    sub.add_parser("usage")
    args = parser.parse_args(argv)
    store = RunStore(args.db)
    if args.command == "list":
        print("\n\n".join(_format_run(r) for r in store.list_runs(args.limit)) or "No runs.")
    elif args.command == "show":
        run = store.get_run(args.run_id)
        if run is None:
            print("Run not found.")
            return 1
        print(_format_run(run))
    else:
        for row in store.usage_by_model():
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
