"""Four-stage analysis workflow with an explicit human editorial gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .providers import (
    AnalysisConfig,
    AnalysisRole,
    ProviderError,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
)


class EditorialStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class StageResult:
    role: AnalysisRole
    response: ProviderResponse


@dataclass(frozen=True, slots=True)
class AnalysisDraft:
    report: str
    collection: StageResult
    main_analysis: StageResult
    counter_analysis: StageResult
    final_report: StageResult
    status: EditorialStatus = EditorialStatus.DRAFT


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    draft: AnalysisDraft
    approved_by: str
    status: EditorialStatus = EditorialStatus.APPROVED


class AnalysisPipeline:
    def __init__(self, config: AnalysisConfig, providers: ProviderRegistry):
        self.config = config
        self.providers = providers

    def _run(self, role: AnalysisRole, prompt: str, context: str) -> StageResult:
        provider, role_config = self.providers.for_role(self.config, role)
        request = ProviderRequest(role=role, prompt=prompt, context=context, model=role_config.model)
        try:
            response = provider.complete(request)
        except ProviderError:
            raise
        except Exception as exc:
            # Avoid leaking vendor exception text that may contain request data or credentials.
            raise ProviderError(f"Provider failed during role '{role.value}'.") from exc
        if not isinstance(response, ProviderResponse) or not response.text.strip():
            raise ProviderError(f"Provider returned an empty or invalid response for role '{role.value}'.")
        return StageResult(role=role, response=response)

    def analyze(self, source_material: str) -> AnalysisDraft:
        if not source_material.strip():
            raise ValueError("Source material must not be empty")

        collection = self._run(
            AnalysisRole.COLLECTION,
            "Organize the supplied public source material; keep facts and uncertainty explicit.",
            source_material,
        )
        main = self._run(
            AnalysisRole.MAIN_ANALYSIS,
            "Analyze the collected material, distinguish evidence from assessment, and state uncertainty.",
            collection.response.text,
        )
        counter = self._run(
            AnalysisRole.COUNTER_ANALYSIS,
            "Independently challenge the main analysis. Identify unsupported claims, omissions, and alternatives. Do not approve or publish it.",
            f"Source material:\n{collection.response.text}\n\nMain analysis to review:\n{main.response.text}",
        )
        final = self._run(
            AnalysisRole.FINAL_REPORT,
            "Prepare an editorial draft. Preserve uncertainty and include unresolved counter-analysis points. This is not approved for publication.",
            f"Main analysis:\n{main.response.text}\n\nIndependent counter-analysis:\n{counter.response.text}",
        )
        return AnalysisDraft(
            report=final.response.text,
            collection=collection,
            main_analysis=main,
            counter_analysis=counter,
            final_report=final,
        )


def approve_for_publication(draft: AnalysisDraft, *, approved_by: str) -> PublicationApproval:
    """Record an explicit editorial decision; providers cannot call this implicitly."""
    editor = approved_by.strip()
    if not editor:
        raise ValueError("A human editor identity is required for publication approval")
    if draft.status is not EditorialStatus.DRAFT:
        raise ValueError("Only a draft can be approved")
    return PublicationApproval(draft=draft, approved_by=editor)
