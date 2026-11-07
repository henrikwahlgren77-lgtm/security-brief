import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.analysis import (
    AnalysisConfig,
    AnalysisPipeline,
    AnalysisRole,
    ProviderError,
    ProviderRegistry,
    RoleConfig,
    build_registry,
)
from app.analysis.context import format_sources_for_prompt
from app.analysis.mock_provider import MockProvider
from app.analysis.prompts import PromptLibrary
from app.analysis.providers import ProviderResponse
from app.analysis.runs import RunStore, main as runs_main
from app.analysis.sources import FetchStatus, SourceRecord, SourceStore

KEY = "sk-live-VERY-SECRET-0123456789"
NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
ROLES = ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")


class TickingClock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def runs(tmp_path):
    return RunStore(tmp_path / "runs.sqlite3", clock=TickingClock())


def mock_config(**overrides):
    roles = {role: RoleConfig(provider="main") for role in AnalysisRole}
    roles.update({AnalysisRole(k): v for k, v in overrides.items()})
    return AnalysisConfig(roles=roles)


def priced_provider(texts):
    """Mock that reports tokens, so cost and totals can be checked."""
    class Priced(MockProvider):
        def complete(self, request):
            base = super().complete(request)
            return ProviderResponse(text=base.text, provider="mock", model="served-1", input_tokens=100,
                                    output_tokens=40, latency_ms=12, attempts=2, finish_reason="stop")
    return Priced(texts)


def test_successful_run_is_fully_recorded(runs):
    provider = priced_provider(["collected", "main", "counter", "final report"])
    config = mock_config(main_analysis=RoleConfig(provider="main", model="req-model", prompt_version="v1",
                                                  price_input_per_mtok=1.0, price_output_per_mtok=2.0))
    draft = AnalysisPipeline(config, ProviderRegistry({"main": provider}), run_store=runs).analyze("SOURCE TEXT")

    run = runs.get_run(draft.run_id)
    assert (run.status, run.report, run.report_status, run.error) == ("completed", "final report", "draft", None)
    assert [s.role for s in run.stages] == ["collection", "main_analysis", "counter_analysis", "final_report"]
    assert [s.position for s in run.stages] == [1, 2, 3, 4]
    first, second = run.stages[0], run.stages[1]
    assert first.input_text == "SOURCE TEXT" and first.output_text == "collected"
    assert second.input_text == "collected" and second.output_text == "main"
    assert (second.requested_model, second.served_model) == ("req-model", "served-1")
    assert second.prompt_version == "v1" and second.prompt_sha256 == PromptLibrary().load(AnalysisRole.MAIN_ANALYSIS).sha256
    assert (second.input_tokens, second.output_tokens, second.latency_ms, second.attempts) == (100, 40, 12, 2)
    assert second.estimated_cost == pytest.approx((100 * 1.0 + 40 * 2.0) / 1_000_000)
    assert run.input_tokens == 400 and run.output_tokens == 160
    assert run.config["main_analysis"]["model"] == "req-model"
    assert run.duration_seconds and run.duration_seconds > 0
    assert "counter" in run.stages[3].input_text  # the final stage received the counter-analysis


def test_cost_totals_are_a_lower_bound_unless_every_stage_is_priced(runs):
    draft = AnalysisPipeline(
        mock_config(main_analysis=RoleConfig(provider="main", price_input_per_mtok=1.0, price_output_per_mtok=1.0)),
        ProviderRegistry({"main": priced_provider([])}), run_store=runs,
    ).analyze("s")
    run = runs.get_run(draft.run_id)
    assert run.known_cost > 0 and run.cost_complete is False  # three stages have no price: unknown, not zero
    all_priced = RoleConfig(provider="main", price_input_per_mtok=1.0, price_output_per_mtok=1.0)
    draft = AnalysisPipeline(mock_config(**{r.lower(): all_priced for r in ROLES}),
                             ProviderRegistry({"main": priced_provider([])}), run_store=runs).analyze("s")
    assert runs.get_run(draft.run_id).cost_complete is True


def test_failed_stage_is_recorded_and_later_stages_never_run(runs):
    provider = MockProvider(fail_on_call=2)
    with pytest.raises(ProviderError, match="Mock failure"):
        AnalysisPipeline(mock_config(), ProviderRegistry({"main": provider}), run_store=runs).analyze("source")
    (run,) = runs.list_runs()
    assert (run.status, run.report) == ("failed", None)
    assert "Mock failure" in run.error
    assert [(s.role, s.status) for s in run.stages] == [("collection", "ok"), ("main_analysis", "failed")]
    assert run.stages[1].error and run.stages[1].input_text and run.stages[1].output_text is None


def test_configuration_failures_are_recorded_too(runs, tmp_path):
    with pytest.raises(ProviderError, match="not registered"):
        AnalysisPipeline(mock_config(collection=RoleConfig(provider="absent")),
                         ProviderRegistry({"main": MockProvider()}), run_store=runs).analyze("source")
    assert runs.list_runs()[0].stages[0].status == "failed"

    with pytest.raises(Exception, match="does not exist"):
        AnalysisPipeline(mock_config(collection=RoleConfig(provider="main", prompt_version="v99")),
                         ProviderRegistry({"main": MockProvider()}), run_store=runs).analyze("source")
    latest = runs.list_runs()[0]
    assert latest.status == "failed" and "v99" in latest.error


def test_unexpected_exception_text_is_not_stored(runs):
    class Leaky(MockProvider):
        def complete(self, request):
            raise RuntimeError(f"upstream said: key {KEY} and prompt {request.context}")

    with pytest.raises(ProviderError):
        AnalysisPipeline(mock_config(), ProviderRegistry({"main": Leaky()}), run_store=runs).analyze("source")
    run = runs.list_runs()[0]
    stored = (run.error or "") + "".join(s.error or "" for s in run.stages)
    assert KEY not in stored and "upstream said" not in stored and "RuntimeError" in stored


def test_empty_source_creates_no_run(runs):
    with pytest.raises(ValueError):
        AnalysisPipeline(mock_config(), ProviderRegistry({"main": MockProvider()}), run_store=runs).analyze("  ")
    assert runs.list_runs() == []


def test_run_records_which_stored_sources_it_used(runs, tmp_path):
    sources = SourceStore(tmp_path / "runs.sqlite3")
    saved = [sources.add(SourceRecord(name="Säpo", url=f"https://www.sakerhetspolisen.se/{n}", fetched_at=NOW,
                                      fetch_status=FetchStatus.OK, content_sha256=str(n) * 64, content_text="t"))
             for n in (1, 2)]
    text, id_map = format_sources_for_prompt(saved)
    draft = AnalysisPipeline(mock_config(), ProviderRegistry({"main": MockProvider()}), run_store=runs).analyze(
        text, source_map=id_map)
    run = runs.get_run(draft.run_id)
    assert run.source_map == {"S1": saved[0].id, "S2": saved[1].id}
    assert run.stages[0].input_text == text  # the exact material is recoverable from the log
    assert len(run.source_material_sha256) == 64


def test_secrets_never_reach_the_database_or_the_log(tmp_path, caplog):
    def route(request):
        body = {"model": "served", "choices": [{"finish_reason": "stop", "message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
        return httpx.Response(200, json=body)

    db = tmp_path / "runs.sqlite3"
    registry = build_registry({"OPENAI_API_KEY": KEY}, transport=httpx.MockTransport(route), sleep=lambda s: None)
    config = AnalysisConfig.from_env({f"ANALYSIS_{r}_PROVIDER": "openai" for r in ROLES}
                                     | {f"ANALYSIS_{r}_MODEL": "m" for r in ROLES})
    with caplog.at_level(logging.INFO, logger="app.analysis.runs"):
        draft = AnalysisPipeline(config, registry, run_store=RunStore(db)).analyze("CONFIDENTIAL SOURCE BODY")

    assert KEY.encode() not in db.read_bytes()
    log = caplog.text
    assert KEY not in log and "CONFIDENTIAL SOURCE BODY" not in log and "answer" not in log
    assert draft.run_id in log and "provider=openai" in log and "model=served" in log
    assert "tokens_in=3" in log and "tokens_out=2" in log and "prompt=v1" in log


def test_log_marks_failures_without_content(runs, caplog):
    with caplog.at_level(logging.INFO, logger="app.analysis.runs"):
        with pytest.raises(ProviderError):
            AnalysisPipeline(mock_config(), ProviderRegistry({"main": MockProvider(fail_on_call=1)}),
                             run_store=runs).analyze("SECRET MATERIAL")
    assert "FAILED" in caplog.text and "SECRET MATERIAL" not in caplog.text


def test_run_log_can_only_hold_drafts_and_finishing_is_one_way(runs):
    draft = AnalysisPipeline(mock_config(), ProviderRegistry({"main": MockProvider()}), run_store=runs).analyze("s")
    assert runs.get_run(draft.run_id).report_status == "draft"
    runs.finish_run(draft.run_id, error="late failure")  # already completed: must not change it
    assert runs.get_run(draft.run_id).status == "completed"
    with pytest.raises(ValueError):
        runs.finish_run(draft.run_id)  # neither report nor error
    with pytest.raises(ValueError):
        runs.record_stage(draft.run_id, position=9, role="x", provider="p", requested_model=None,
                          prompt_version=None, prompt_sha256=None, input_text="", started_at=NOW)


def test_history_survives_reopening_and_lists_newest_first(tmp_path):
    path, clock = tmp_path / "runs.sqlite3", TickingClock()
    ids = []
    for _ in range(3):
        pipeline = AnalysisPipeline(mock_config(), ProviderRegistry({"main": MockProvider()}),
                                    run_store=RunStore(path, clock=clock))
        ids.append(pipeline.analyze("s").run_id)
    reopened = RunStore(path).list_runs(limit=2)
    assert [r.id for r in reopened] == [ids[2], ids[1]]


def test_usage_by_model_aggregates_tokens_and_counts_failures(runs):
    ok = AnalysisPipeline(mock_config(), ProviderRegistry({"main": priced_provider([])}), run_store=runs)
    ok.analyze("s")
    ok.analyze("s")
    with pytest.raises(ProviderError):
        AnalysisPipeline(mock_config(), ProviderRegistry({"main": MockProvider(fail_on_call=1)}), run_store=runs).analyze("s")
    rows = {(r["provider"], r["model"]): r for r in runs.usage_by_model()}
    served = rows[("main", "served-1")]
    assert (served["calls"], served["input_tokens"], served["output_tokens"]) == (8, 800, 320)
    assert served["calls_without_cost"] == 8 and served["known_cost"] is None
    assert rows[("main", None)]["failed_calls"] == 1
    assert runs.usage_by_model(since=NOW + timedelta(days=1)) == []


def test_cli_prints_metadata_but_never_content(runs, capsys):
    draft = AnalysisPipeline(mock_config(), ProviderRegistry({"main": priced_provider(["x", "y", "z", "TOP SECRET DRAFT"])}),
                             run_store=runs).analyze("TOP SECRET SOURCE")
    assert runs_main(["--db", runs.path, "list"]) == 0
    assert runs_main(["--db", runs.path, "show", draft.run_id]) == 0
    assert runs_main(["--db", runs.path, "show", "missing"]) == 1
    output = capsys.readouterr().out
    assert draft.run_id in output and "completed" in output and "prompt v1" in output
    assert "TOP SECRET" not in output
