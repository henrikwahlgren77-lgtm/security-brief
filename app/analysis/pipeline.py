"""Four-stage analysis workflow with an explicit human editorial gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .prompts import PromptLibrary
from .providers import (
    AnalysisConfig,
    AnalysisRole,
    ProviderError,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
    estimate_cost,
)


class EditorialStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class StageResult:
    role: AnalysisRole
    response: ProviderResponse
    prompt_version: str = ""
    prompt_sha256: str = ""
    estimated_cost: float | None = None  # None = prices or token counts unknown


@dataclass(frozen=True, slots=True)
class AnalysisDraft:
    report: str
    collection: StageResult
    main_analysis: StageResult
    counter_analysis: StageResult
    final_report: StageResult
    status: EditorialStatus = EditorialStatus.DRAFT
    warnings: tuple[str, ...] = ()

    @property
    def stages(self) -> tuple[StageResult, ...]:
        return (self.collection, self.main_analysis, self.counter_analysis, self.final_report)


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    draft: AnalysisDraft
    approved_by: str
    status: EditorialStatus = EditorialStatus.APPROVED


class AnalysisPipeline:
    def __init__(self, config: AnalysisConfig, providers: ProviderRegistry, prompts: PromptLibrary | None = None):
        self.config = config
        self.providers = providers
        self.prompts = prompts or PromptLibrary()

    def _run(self, role: AnalysisRole, context: str) -> StageResult:
        provider, role_config = self.providers.for_role(self.config, role)
        prompt = self.prompts.load(role, role_config.prompt_version)
        request = ProviderRequest(
            role=role,
            prompt=prompt.text,
            context=context,
            model=role_config.model,
            max_output_tokens=role_config.max_output_tokens,
        )
        try:
            response = provider.complete(request)
        except ProviderError:
            raise
        except Exception as exc:
            # Avoid leaking vendor exception text that may contain request data or credentials.
            raise ProviderError(f"Provider failed during role '{role.value}'.") from exc
        if not isinstance(response, ProviderResponse) or not response.text.strip():
            raise ProviderError(f"Provider returned an empty or invalid response for role '{role.value}'.")
        return StageResult(
            role=role,
            response=response,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            estimated_cost=estimate_cost(response, role_config),
        )

    def analyze(self, source_material: str) -> AnalysisDraft:
        if not source_material.strip():
            raise ValueError("Source material must not be empty")

        collection = self._run(AnalysisRole.COLLECTION, source_material)
        main = self._run(AnalysisRole.MAIN_ANALYSIS, collection.response.text)
        counter = self._run(
            AnalysisRole.COUNTER_ANALYSIS,
            f"Source material:\n{collection.response.text}\n\nMain analysis to review:\n{main.response.text}",
        )
        final = self._run(
            AnalysisRole.FINAL_REPORT,
            f"Main analysis:\n{main.response.text}\n\nIndependent counter-analysis:\n{counter.response.text}",
        )
        return AnalysisDraft(
            report=final.response.text,
            collection=collection,
            main_analysis=main,
            counter_analysis=counter,
            final_report=final,
            warnings=self._independence_warnings(main, counter),
        )

    @staticmethod
    def _independence_warnings(main: StageResult, counter: StageResult) -> tuple[str, ...]:
        a, b = main.response, counter.response
        if a.provider == "mock" or b.provider == "mock":
            return ()
        if a.provider == b.provider and a.model == b.model:
            return (
                "The counter-analysis ran on the same provider and model as the main analysis; "
                "it is not an independent check.",
            )
        return ()


def approve_for_publication(draft: AnalysisDraft, *, approved_by: str) -> PublicationApproval:
    """Record an explicit editorial decision; providers cannot call this implicitly."""
    editor = approved_by.strip()
    if not editor:
        raise ValueError("A human editor identity is required for publication approval")
    if draft.status is not EditorialStatus.DRAFT:
        raise ValueError("Only a draft can be approved")
    return PublicationApproval(draft=draft, approved_by=editor)
