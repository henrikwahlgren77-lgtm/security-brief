import pytest

from app.analysis import (
    AnalysisConfig,
    AnalysisPipeline,
    AnalysisRole,
    EditorialStatus,
    ProviderError,
    ProviderRegistry,
    RoleConfig,
    approve_for_publication,
)
from app.analysis.mock_provider import MockProvider


def config_for(**providers):
    roles = {role: RoleConfig(provider="main") for role in AnalysisRole}
    for role, provider in providers.items():
        roles[AnalysisRole(role)] = RoleConfig(provider=provider)
    return AnalysisConfig(roles=roles)


def test_provider_can_be_changed_per_role_without_pipeline_changes():
    primary = MockProvider(["collected", "main analysis", "final draft"])
    reviewer = MockProvider(["counter points"])
    config = config_for(counter_analysis="reviewer")

    result = AnalysisPipeline(config, ProviderRegistry({"main": primary, "reviewer": reviewer})).analyze(
        "public source material"
    )

    assert result.report == "final draft"
    assert [call.role for call in primary.calls] == [
        AnalysisRole.COLLECTION,
        AnalysisRole.MAIN_ANALYSIS,
        AnalysisRole.FINAL_REPORT,
    ]
    assert [call.role for call in reviewer.calls] == [AnalysisRole.COUNTER_ANALYSIS]
    assert result.status is EditorialStatus.DRAFT


def test_model_and_provider_roles_are_configured_from_environment_only():
    config = AnalysisConfig.from_env(
        {
            "ANALYSIS_MAIN_ANALYSIS_PROVIDER": "anthropic",
            "ANALYSIS_MAIN_ANALYSIS_MODEL": "configured-by-operator",
            "ANALYSIS_COUNTER_ANALYSIS_PROVIDER": "mistral",
            "OPENAI_API_KEY": "must-not-be-loaded-into-config",
        }
    )

    assert config.for_role(AnalysisRole.MAIN_ANALYSIS) == RoleConfig(
        provider="anthropic", model="configured-by-operator"
    )
    assert config.for_role(AnalysisRole.COUNTER_ANALYSIS) == RoleConfig(provider="mistral")
    assert "must-not-be-loaded-into-config" not in repr(config)


def test_unregistered_provider_fails_clearly():
    pipeline = AnalysisPipeline(config_for(collection="not-installed"), ProviderRegistry({"mock": MockProvider()}))

    with pytest.raises(ProviderError, match="not registered"):
        pipeline.analyze("source")


def test_provider_failure_stops_workflow_and_does_not_return_a_draft():
    failing = MockProvider(fail_on_call=2)
    pipeline = AnalysisPipeline(config_for(), ProviderRegistry({"main": failing}))

    with pytest.raises(ProviderError, match="Mock failure"):
        pipeline.analyze("source")
    assert len(failing.calls) == 2


def test_counter_analysis_is_review_input_and_never_approves_main_analysis():
    provider = MockProvider(["collected", "main", "counter review", "final"])
    result = AnalysisPipeline(config_for(), ProviderRegistry({"main": provider})).analyze("source")

    assert result.status is EditorialStatus.DRAFT
    assert "counter review" in provider.calls[-1].context
    assert "godkänn inte" in provider.calls[2].prompt.lower()
    with pytest.raises(ValueError, match="human editor"):
        approve_for_publication(result, approved_by=" ")
    approval = approve_for_publication(result, approved_by="editor@example.test")
    assert approval.status is EditorialStatus.APPROVED
    assert approval.approved_by == "editor@example.test"


def test_empty_source_is_rejected_before_provider_calls():
    provider = MockProvider()
    pipeline = AnalysisPipeline(config_for(), ProviderRegistry({"main": provider}))

    with pytest.raises(ValueError, match="must not be empty"):
        pipeline.analyze("  ")
    assert provider.calls == []
