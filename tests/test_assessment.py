import json
from datetime import datetime, timedelta, timezone

import pytest

from app.analysis.assessment import (
    AssessmentError,
    Article5Category,
    Change,
    Classification,
    PreviousClaim,
    PreviousContext,
    Severity,
    calibrate,
    check_counter,
    ensure_disagreement_reported,
    format_previous,
    parse_counter,
    parse_main,
    split_structured,
    unresolved,
)
from app.analysis.sources import FetchStatus, OriginType, SourceRecord

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
WINDOW = NOW - timedelta(hours=24)


def record(n, *, tier=1, origin=OriginType.ORIGINAL, original_url=None, published=None, event=None, url=None):
    return SourceRecord(
        name=f"Källa {n}", url=url or f"https://www.example-{n}.test/page", fetched_at=NOW, fetch_status=FetchStatus.OK,
        content_sha256=str(n) * 64, content_text="text", tier=tier, origin_type=origin, original_url=original_url,
        published_at=published, event_date=event,
    )


def claim(id="C1", text="Något hände.", kind="fact", cls="confirmed", sources=("S1",), change="new", refs=(), who=None):
    return {"id": id, "text": text, "kind": kind, "attributed_to": who, "classification": cls,
            "sources": list(sources), "change": change, "previous_refs": list(refs)}


def main_json(claims=(), article5=None, hypotheses=(), indicators=None):
    return {
        "claims": list(claims),
        "article5": article5 or {"category": "no_basis", "reasoning": "Inget underlag.", "sources": []},
        "hypotheses": list(hypotheses),
        "indicators": indicators or {"escalation": [], "deescalation": []},
    }


def assess(claims=(), records=(), previous=None, window=WINDOW, **kwargs):
    main = parse_main(f"prosa\n```json\n{json.dumps(main_json(claims, **kwargs))}\n```")[1]
    id_map = {f"S{i}": r for i, r in enumerate(records, start=1)}
    return calibrate(main, id_map, previous=previous, window_start=window)


def only(calibrated):
    return calibrated.claims[0].claim


def codes(calibrated):
    return {i.code for i in calibrated.issues}


def warnings_of(calibrated):
    """Issues that need the editor's attention (info notes are context only)."""
    return [i.code for i in calibrated.issues if i.level == "warn"]


def previous_with(*claims):
    return PreviousContext({f"P{i}": PreviousClaim(f"P{i}", text, "confirmed", tuple(urls))
                            for i, (text, urls) in enumerate(claims, start=1)}, NOW - timedelta(days=1))


# --- parsing ------------------------------------------------------------------

def test_valid_block_is_parsed_and_prose_is_separated():
    prose, parsed = parse_main(f"## Händelser\nText.\n```json\n{json.dumps(main_json([claim()]))}\n```\n")
    assert prose == "## Händelser\nText." and parsed.claims[0].id == "C1"


def test_the_last_block_wins():
    text = f"```json\n{{\"x\": 1}}\n```\ntext\n```json\n{json.dumps(main_json())}\n```"
    assert split_structured(text)[1]["claims"] == []


@pytest.mark.parametrize("text", ["no block at all", "```json\n{not json}\n```", "```json\n{}\n```"])
def test_missing_or_broken_blocks_are_rejected(text):
    with pytest.raises(AssessmentError):
        parse_main(text)


@pytest.mark.parametrize("mutate", [
    lambda d: d["claims"].append({**claim(), "surprise": 1}),              # unknown field
    lambda d: d["claims"].append(claim(id="X1")),                           # bad claim id
    lambda d: d["claims"].extend([claim(), claim()]),                       # duplicate ids
    lambda d: d["claims"].append(claim(sources=("http://evil.test",))),     # sources must be S ids, not URLs
    lambda d: d["claims"].append(claim(cls="certain")),                     # unknown classification
    lambda d: d["claims"].append(claim(change="brand-new")),
    lambda d: d["claims"].append(claim(text="")),
    lambda d: d["hypotheses"].append({"event": "e", "explanations": [{"text": "a"}], "strongest": 0}),  # needs two
    lambda d: d["hypotheses"].append({"event": "e", "explanations": [{"text": "a"}, {"text": "b"}], "strongest": 5}),
    lambda d: d["article5"].update(category="war"),
])
def test_schema_violations_are_rejected(mutate):
    data = main_json()
    mutate(data)
    with pytest.raises(AssessmentError, match="schema"):
        parse_main(f"```json\n{json.dumps(data)}\n```")


def test_validation_errors_never_echo_model_output():
    data = main_json([claim(id="SECRET-ID-DO-NOT-LEAK")])
    with pytest.raises(AssessmentError) as info:
        parse_main(f"```json\n{json.dumps(data)}\n```")
    assert "SECRET-ID-DO-NOT-LEAK" not in str(info.value) and "claims.0.id" in str(info.value)


def test_counter_block_schema():
    ok = {"objections": [{"id": "O1", "target": "C1", "type": "unsupported", "severity": "decisive", "text": "t"}]}
    assert parse_counter(f"x\n```json\n{json.dumps(ok)}\n```")[1].objections[0].severity is Severity.DECISIVE
    for bad in ({"objections": [{**ok["objections"][0], "id": "1"}]},
                {"objections": [ok["objections"][0], ok["objections"][0]]},
                {"objections": [{**ok["objections"][0], "severity": "huge"}]}):
        with pytest.raises(AssessmentError):
            parse_counter(f"```json\n{json.dumps(bad)}\n```")


# --- evidence rules -----------------------------------------------------------

def test_confirmed_with_a_primary_source_stays_confirmed():
    result = assess([claim()], [record(1, tier=1)])
    assert only(result).classification is Classification.CONFIRMED and warnings_of(result) == []


def test_confirmed_with_one_non_primary_source_is_downgraded():
    result = assess([claim()], [record(1, tier=None)])
    assert only(result).classification is Classification.CREDIBLE
    assert result.claims[0].downgraded_from is Classification.CONFIRMED
    assert "insufficient_verification" in codes(result)


def test_two_republications_of_one_original_do_not_make_two_independent_sources():
    original = "https://authority.test/press/1"
    records = [record(1, tier=None, origin=OriginType.REPUBLICATION, original_url=original),
               record(2, tier=None, origin=OriginType.REPUBLICATION, original_url=original)]
    result = assess([claim(sources=("S1", "S2"))], records)
    assert only(result).classification is Classification.CREDIBLE and "insufficient_verification" in codes(result)


def test_two_independent_non_primary_sources_can_confirm():
    result = assess([claim(sources=("S1", "S2"))], [record(1, tier=None), record(2, tier=None)])
    assert only(result).classification is Classification.CONFIRMED


def test_republication_is_not_a_primary_source_even_at_tier_1():
    result = assess([claim()], [record(1, tier=1, origin=OriginType.REPUBLICATION, original_url="https://o.test/x")])
    assert only(result).classification is Classification.CREDIBLE


def test_factual_claim_without_a_provided_source_cannot_be_supported():
    result = assess([claim(sources=()), claim(id="C2", cls="credible_unconfirmed", sources=())], [])
    assert [c.claim.classification for c in result.claims] == [Classification.UNCONFIRMED] * 2
    assert "no_source" in codes(result)


def test_invented_source_ids_are_flagged_and_do_not_count():
    result = assess([claim(sources=("S1", "S9"))], [record(1, tier=None)])
    assert "unknown_source" in codes(result) and only(result).classification is Classification.CREDIBLE


def test_own_analysis_is_never_confirmed_but_may_stand_without_sources():
    result = assess([claim(kind="own_analysis", sources=())], [])
    assert only(result).classification is Classification.CREDIBLE and "analysis_not_fact" in codes(result)
    result = assess([claim(kind="own_analysis", cls="unconfirmed", sources=())], [])
    assert warnings_of(result) == []


def test_disputed_and_unconfirmed_are_left_as_the_model_set_them():
    result = assess([claim(cls="disputed"), claim(id="C2", cls="unconfirmed")], [record(1)])
    assert [c.claim.classification for c in result.claims] == [Classification.DISPUTED, Classification.UNCONFIRMED]


# --- change and time rules ----------------------------------------------------

def test_without_an_earlier_report_nothing_can_be_new():
    result = assess([claim(change="new"), claim(id="C2", change="background"), claim(id="C3", change="continuing", refs=("P1",))],
                    [record(1)])
    assert [c.claim.change for c in result.claims] == [Change.NO_COMPARISON, Change.BACKGROUND, Change.NO_COMPARISON]
    assert "no_previous_report" in codes(result)


def test_change_relative_to_the_previous_report_needs_a_real_reference():
    previous = previous_with(("Gammal uppgift om något annat.", []))
    result = assess([claim(change="continuing"), claim(id="C2", change="continuing", refs=("P1",)),
                     claim(id="C3", change="denial", refs=("P7",))], [record(1)], previous=previous)
    assert [c.claim.change for c in result.claims] == [Change.NO_COMPARISON, Change.CONTINUING, Change.NO_COMPARISON]
    assert "missing_previous_ref" in codes(result)


def test_new_claim_resting_only_on_already_used_sources_is_a_repeat():
    source = record(1, url="https://www.example-1.test/page?utm_source=mail")
    previous = previous_with(("Helt annan formulering.", ["https://www.example-1.test/page"]))
    result = assess([claim(change="new")], [source], previous=previous)
    assert only(result).change is Change.CONTINUING and "repeat_sources" in codes(result)


def test_new_claim_with_near_identical_wording_is_a_repeat():
    previous = previous_with(("Ryska fartyg observerades nära Gotland under natten", []))
    result = assess([claim(text="Ryska fartyg observerades nära Gotland under natten.", change="new")],
                    [record(2)], previous=previous)
    assert only(result).change is Change.CONTINUING and "repeat_text" in codes(result)


def test_genuinely_new_claim_stays_new():
    previous = previous_with(("Ryska fartyg observerades nära Gotland.", ["https://other.test/a"]))
    result = assess([claim(text="Finland stängde en gränsövergång.", change="new")], [record(3)], previous=previous)
    assert only(result).change is Change.NEW and warnings_of(result) == []


def test_old_events_are_not_presented_as_new():
    previous = previous_with(("Något helt annat.", []))
    old = (NOW - timedelta(days=3)).isoformat()
    result = assess([claim(change="new")], [record(1, published=old)], previous=previous)
    assert only(result).change is Change.BACKGROUND and "old_presented_as_new" in codes(result)
    result = assess([claim(change="new")], [record(1, event="2026-10-01")], previous=previous)
    assert only(result).change is Change.BACKGROUND


def test_one_recent_source_is_enough_to_keep_new():
    previous = previous_with(("Något helt annat.", []))
    result = assess([claim(sources=("S1", "S2"), change="new")],
                    [record(1, published=(NOW - timedelta(days=3)).isoformat()), record(2, published=NOW.isoformat())],
                    previous=previous)
    assert only(result).change is Change.NEW


def test_missing_dates_cannot_prove_newness_and_are_reported():
    previous = previous_with(("Något helt annat.", []))
    result = assess([claim(change="new")], [record(1)], previous=previous)
    assert only(result).change is Change.NEW
    assert [i.level for i in result.issues if i.code == "date_unknown"] == ["info"]


# --- article 5 and numeric risk -----------------------------------------------

def test_article5_category_without_sources_is_reset_to_no_basis():
    a5 = {"category": "attack", "reasoning": "Tolkning.", "sources": ["S9"]}
    result = assess(article5=a5)
    assert result.assessment.article5.category is Article5Category.NO_BASIS and "article5_no_source" in codes(result)


def test_attack_without_a_primary_source_is_flagged():
    a5 = {"category": "attack", "reasoning": "Rapporterad attack.", "sources": ["S1"]}
    result = assess(article5=a5, records=[record(1, tier=None)])
    assert result.assessment.article5.category is Article5Category.ATTACK and "article5_attack_no_primary" in codes(result)
    assert "article5_attack_no_primary" not in codes(assess(article5=a5, records=[record(1, tier=1)]))


@pytest.mark.parametrize("where, kwargs", [
    ("article5", {"article5": {"category": "no_basis", "reasoning": "Risken är 30 % högre.", "sources": []}}),
    ("indicators", {"indicators": {"escalation": ["sannolikhet 12,5 procent"], "deescalation": []}}),
    ("hypotheses", {"hypotheses": [{"event": "e", "strongest": 0, "explanations": [{"text": "a, 40 %"}, {"text": "b"}]}]}),
])
def test_numeric_risk_figures_are_flagged(where, kwargs):
    result = assess(**kwargs)
    assert [i.subject for i in result.issues if i.code == "numeric_risk"] == [where]


def test_a_percentage_inside_a_factual_claim_is_not_a_risk_figure():
    assert warnings_of(assess([claim(text="Försvarsbudgeten ökar med 5 %.")], [record(1)])) == []


def test_rendered_list_tells_later_stages_what_the_system_changed():
    result = assess([claim(), claim(id="C2", change="continuing", refs=("P1",))], [record(1, tier=None)])
    text = result.render()
    assert "nedgraderad från confirmed av systemet" in text and "förändringstyp rättad från continuing" in text
    assert "Artikel 5-kategori: no_basis" in text
    stored = result.to_dict()
    assert stored["claims"][0]["downgraded_from"] == "confirmed" and stored["claims"][0]["source_urls"]
    assert format_previous(None).startswith("Föregående rapport: ingen tillgänglig")
    assert "[P1] (confirmed)" in format_previous(previous_with(("x", [])))


# --- disagreement -------------------------------------------------------------

def objection(id, severity="decisive", target="C1", text="Stödet saknas."):
    return parse_counter(json.dumps({"objections": [{"id": id, "target": target, "type": "unsupported",
                                                     "severity": severity, "text": text}]}).join(["```json\n", "\n```"])).__getitem__(1).objections[0]


REPORT = """## Sammanfattning
- Punkt.

## Osäkerheter och luckor
- Redan nämnt (O1) av granskningen.

## Källor
- [S1]
"""


def test_only_decisive_and_substantial_objections_must_reach_the_editor():
    objections = [objection("O1", "decisive"), objection("O2", "substantial"), objection("O3", "minor")]
    assert [o.id for o in unresolved(objections)] == ["O1", "O2"]


def test_objections_already_in_the_uncertainty_section_are_not_duplicated():
    report, added = ensure_disagreement_reported(REPORT, [objection("O1")])
    assert (report, added) == (REPORT, [])


def test_omitted_objections_are_added_to_the_uncertainty_section():
    report, added = ensure_disagreement_reported(
        REPORT, [objection("O1"), objection("O2", "substantial", "C3", "Alternativ förklaring saknas."),
                 objection("O3", "minor", text="Småsak.")])
    assert added == ["O2"]
    section = report.split("## Osäkerheter och luckor")[1].split("## Källor")[0]
    assert "O2 (Väsentlig, avser C3): Alternativ förklaring saknas." in section and "tillagd av systemet" in section
    assert "Småsak" not in report and "Redan nämnt (O1)" in report
    assert report.index("## Osäkerheter och luckor") < report.index("O2") < report.index("## Källor")
    assert report.count("## ") == REPORT.count("## ")  # no headings added or lost


def test_mentioning_an_objection_in_another_section_does_not_count():
    report = REPORT.replace("(O1) ", "").replace("## Källor", "## Redaktionell analys\nSe O1.\n\n## Källor")
    assert ensure_disagreement_reported(report, [objection("O1")])[1] == ["O1"]


def test_o1_is_not_satisfied_by_o10():
    report = REPORT.replace("(O1)", "(O10)")
    assert ensure_disagreement_reported(report, [objection("O1")])[1] == ["O1"]


def test_missing_uncertainty_section_is_created_before_sources():
    report, added = ensure_disagreement_reported("## Sammanfattning\n- x\n\n## Källor\n- [S1]\n", [objection("O1")])
    assert added == ["O1"]
    assert report.index("## Osäkerheter och luckor") < report.index("O1") < report.index("## Källor")
    report, _ = ensure_disagreement_reported("## Sammanfattning\n- x\n", [objection("O1")])
    assert report.rstrip().endswith("_(tillagd av systemet)_") and "## Osäkerheter och luckor" in report


def test_unknown_objection_targets_are_reported_as_info():
    counter = parse_counter('```json\n{"objections": [{"id": "O1", "target": "C9", "type": "missing", "severity": "minor", "text": "t"}]}\n```')[1]
    calibrated = assess([claim()], [record(1)])
    issues = check_counter(counter, calibrated)
    assert [(i.code, i.level) for i in issues] == [("unknown_target", "info")]


def test_prompts_and_schema_cannot_drift_apart():
    from app.analysis.assessment import (Article5Category, ClaimKind, ObjectionType, Verdict)
    from app.analysis.prompts import PromptLibrary
    from app.analysis.providers import AnalysisRole

    main = PromptLibrary().load(AnalysisRole.MAIN_ANALYSIS).text
    counter = PromptLibrary().load(AnalysisRole.COUNTER_ANALYSIS).text
    for enum in (Classification, ClaimKind, Change, Article5Category):
        for value in enum:
            assert value.value in main, f"main_analysis prompt does not mention {value.value}"
    for enum in (ObjectionType, Severity, Verdict):
        for value in enum:
            assert value.value in counter, f"counter_analysis prompt does not mention {value.value}"
    for word in ("claims", "article5", "hypotheses", "indicators", "previous_refs", "strongest"):
        assert word in main
    for word in ("objections", "section_verdicts", "disagreements", "target"):
        assert word in counter
    final = PromptLibrary().load(AnalysisRole.FINAL_REPORT).text
    assert "kontrollerade listan gäller" in final and "decisive eller substantial" in final
