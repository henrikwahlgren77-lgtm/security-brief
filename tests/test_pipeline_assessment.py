import json
from datetime import datetime, timedelta, timezone

import pytest

from app.analysis import AnalysisConfig, AnalysisPipeline, AnalysisRole, ProviderRegistry, RoleConfig, RunStore
from app.analysis.assessment import AssessmentError, Change, Classification, PreviousClaim, PreviousContext
from app.analysis.context import format_sources_for_prompt
from app.analysis.mock_provider import MockProvider, with_block
from app.analysis.sources import FetchStatus, OriginType, SourceRecord, SourceStore

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
SECRET = "SECRET-MODEL-TEXT-DO-NOT-STORE"


@pytest.fixture
def env(tmp_path):
    return RunStore(tmp_path / "runs.sqlite3"), SourceStore(tmp_path / "runs.sqlite3")


def source(store, n, *, tier=None, published=None):
    return store.add(SourceRecord(
        name=f"Källa {n}", url=f"https://www.example-{n}.test/page", fetched_at=NOW, fetch_status=FetchStatus.OK,
        content_sha256=str(n) * 64, content_text="text", tier=tier, origin_type=OriginType.ORIGINAL, published_at=published))


def claim(id="C1", text="Något hände vid Gotland.", cls="confirmed", sources=("S1",), change="new", refs=()):
    return {"id": id, "text": text, "kind": "fact", "attributed_to": None, "classification": cls,
            "sources": list(sources), "change": change, "previous_refs": list(refs)}


def main_text(*claims, prose="huvudanalys"):
    data = {"claims": list(claims), "article5": {"category": "no_basis", "reasoning": "Inget underlag.", "sources": []},
            "hypotheses": [], "indicators": {"escalation": [], "deescalation": []}}
    return with_block(prose, json.dumps(data))


def counter_text(*objections, prose="granskning"):
    return with_block(prose, json.dumps({"objections": list(objections), "section_verdicts": [], "disagreements": []}))


def objection(id, severity="decisive", target="C1", text="Stödet räcker inte."):
    return {"id": id, "target": target, "type": "unsupported", "severity": severity, "text": text}


def pipeline(provider, runs=None):
    config = AnalysisConfig(roles={role: RoleConfig(provider="main") for role in AnalysisRole})
    return AnalysisPipeline(config, ProviderRegistry({"main": provider}), run_store=runs)


FINAL = "## Sammanfattning\n- Punkt.\n\n## Osäkerheter och luckor\n- Inget särskilt.\n\n## Källor\n- [S1]\n"


def test_system_calibration_reaches_the_reviewer_the_writer_and_the_draft(env):
    runs, sources = env
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=None)])
    provider = MockProvider(["collected", main_text(claim()), counter_text(), FINAL])
    draft = pipeline(provider, runs).analyze(text, source_map=id_map)

    assert draft.assessment.claims[0].claim.classification is Classification.CREDIBLE
    assert "nedgraderad från confirmed av systemet" in provider.calls[2].context   # reviewer sees it
    assert "nedgraderad från confirmed av systemet" in provider.calls[3].context   # writer sees it
    assert any("insufficient_verification" in w for w in draft.warnings)
    assert "no_comparison" in provider.calls[2].context                           # no earlier report: not 'new'


def test_omitted_disagreement_is_added_and_recorded(env):
    runs, sources = env
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=1)])
    provider = MockProvider(["collected", main_text(claim()), counter_text(objection("O1"), objection("O2", "minor", text="Småsak.")), FINAL])
    draft = pipeline(provider, runs).analyze(text, source_map=id_map)

    assert draft.added_objections == ("O1",)
    assert "O1 (Avgörande, avser C1): Stödet räcker inte. _(tillagd av systemet)_" in draft.report
    assert "Småsak" not in draft.report
    assert any("disagreement_added" in w for w in draft.warnings)
    assert "O1" in provider.calls[3].context and "Objections (report these by id)" in provider.calls[3].context
    run = runs.get_run(draft.run_id)
    assert run.report == draft.report and run.status == "completed" and run.report_status == "draft"
    assert [o["id"] for o in run.assessment["objections"]] == ["O1", "O2"]
    assert run.assessment["added_objections"] == ["O1"]
    assert {i["code"] for i in run.assessment["issues"]} >= {"disagreement_added"}


def test_disagreement_the_writer_reported_is_not_duplicated(env):
    final = FINAL.replace("- Inget särskilt.", "- Huvudanalysen bedömer X; granskningen invänder (O1) att stödet saknas.")
    provider = MockProvider(["collected", main_text(), counter_text(objection("O1")), final])
    draft = pipeline(provider).analyze("material")
    assert draft.added_objections == () and draft.report == final


def test_a_malformed_structured_answer_gets_one_corrective_retry(env):
    runs, _ = env
    provider = MockProvider(["collected", "analys utan block", main_text(), counter_text(), FINAL])
    draft = pipeline(provider, runs).analyze("material")
    assert any("format correction" in w for w in draft.warnings)
    retry_context = provider.calls[2].context
    assert "Ditt förra svar kunde inte tolkas" in retry_context and "no ```json block" in retry_context
    run = runs.get_run(draft.run_id)
    assert [s.role for s in run.stages] == ["collection", "main_analysis", "main_analysis", "counter_analysis", "final_report"]
    assert [s.position for s in run.stages] == [1, 2, 3, 4, 5]


def test_structured_output_invalid_twice_stops_the_run_without_leaking_model_text(env):
    runs, _ = env
    bad = with_block("x", json.dumps({"claims": SECRET}))
    provider = MockProvider(["collected", bad, bad, "never reached"])
    with pytest.raises(AssessmentError, match="invalid twice") as info:
        pipeline(provider, runs).analyze("material")
    assert len(provider.calls) == 3  # the counter-analysis never ran
    run = runs.list_runs()[0]
    assert run.status == "failed" and run.report is None and "invalid twice" in run.error
    assert SECRET not in str(info.value) and SECRET not in run.error
    assert SECRET in run.stages[1].output_text  # kept in the stage log for the editor, not in error text


def test_history_decides_what_is_new(env):
    runs, sources = env
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=1)])
    first = pipeline(MockProvider(["c", main_text(claim()), counter_text(), FINAL]), runs).analyze(text, source_map=id_map)
    assert first.assessment.claims[0].claim.change is Change.NO_COMPARISON

    provider = MockProvider(["c", main_text(claim(text="Helt annan formulering.", change="new")), counter_text(), FINAL])
    second = pipeline(provider, runs).analyze(text, source_map=id_map)
    assert "[P1] (confirmed) Något hände vid Gotland." in provider.calls[1].context
    assert second.assessment.claims[0].claim.change is Change.CONTINUING     # same source as yesterday: not new
    assert any("repeat_sources" in w for w in second.warnings)


def test_failed_runs_are_never_used_as_history(env):
    runs, sources = env
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=1)])
    pipeline(MockProvider(["c", main_text(claim()), counter_text(), FINAL]), runs).analyze(text, source_map=id_map)
    with pytest.raises(Exception):
        pipeline(MockProvider(["c", main_text(claim(text="Ofullständig körning")), counter_text()], fail_on_call=4), runs).analyze(text, source_map=id_map)
    provider = MockProvider(["c", main_text(), counter_text(), FINAL])
    pipeline(provider, runs).analyze(text, source_map=id_map)
    assert "Något hände vid Gotland." in provider.calls[1].context and "Ofullständig" not in provider.calls[1].context


def test_history_can_be_switched_off(env):
    runs, sources = env
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=1)])
    pipeline(MockProvider(["c", main_text(claim()), counter_text(), FINAL]), runs).analyze(text, source_map=id_map)
    provider = MockProvider(["c", main_text(), counter_text(), FINAL])
    pipeline(provider, runs).analyze(text, source_map=id_map, use_history=False)
    assert "ingen tillgänglig" in provider.calls[1].context


def test_an_old_event_cannot_be_reported_as_new(env):
    runs, sources = env
    old = (NOW - timedelta(days=3)).isoformat()
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=1, published=old)])
    previous = PreviousContext({"P1": PreviousClaim("P1", "Något helt annat.", "confirmed", ())}, NOW)
    provider = MockProvider(["c", main_text(claim(change="new")), counter_text(), FINAL])
    draft = pipeline(provider, runs).analyze(text, source_map=id_map, previous=previous, window_start=NOW - timedelta(hours=24))
    assert draft.assessment.claims[0].claim.change is Change.BACKGROUND
    assert any("old_presented_as_new" in w for w in draft.warnings)
    assert "background" in provider.calls[2].context


def test_citing_a_source_that_was_never_provided_is_flagged(env):
    runs, sources = env
    text, id_map = format_sources_for_prompt([source(sources, 1, tier=1)])
    provider = MockProvider(["c", main_text(claim(sources=("S1", "S7"))), counter_text(), FINAL])
    draft = pipeline(provider, runs).analyze(text, source_map=id_map)
    assert any("unknown_source" in w and "S7" in w for w in draft.warnings)


def test_the_result_is_always_a_draft_the_pipeline_cannot_publish(env):
    draft = pipeline(MockProvider(["c", main_text(), counter_text(), FINAL])).analyze("material")
    assert draft.status.value == "draft" and draft.run_id is None
    assert not any(hasattr(draft, name) for name in ("publish", "approve", "approved_by"))


def test_an_unfinished_run_with_a_saved_assessment_is_not_history(env):
    runs, _ = env
    config = AnalysisConfig(roles={role: RoleConfig(provider="main") for role in AnalysisRole})
    stuck = runs.start_run(config, "material")  # saved an assessment, then never finished
    runs.save_assessment(stuck, {"claims": [{"text": "Halvfärdig uppgift", "classification": "confirmed", "source_urls": []}]})
    assert runs.latest_previous() is None
    runs.finish_run(stuck, error="crashed")
    assert runs.latest_previous() is None

    done = runs.start_run(config, "material")
    runs.save_assessment(done, {"claims": [{"text": "Färdig uppgift", "classification": "confirmed", "source_urls": ["https://a.test/x"]}]})
    runs.finish_run(done, report="rapport")
    previous = runs.latest_previous()
    assert [c.text for c in previous.claims.values()] == ["Färdig uppgift"] and previous.urls == {"https://a.test/x"}
