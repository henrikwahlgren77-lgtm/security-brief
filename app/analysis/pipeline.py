"""Four-stage analysis workflow with an explicit human editorial gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping

from .prompts import PromptError, PromptLibrary
from .providers import (
    AnalysisConfig,
    AnalysisRole,
    ProviderError,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
    estimate_cost,
)
from .runs import RunStore
from .sources import SourceRecord


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
    run_id: str | None = None  # set when the pipeline has a run log

    @property
    def stages(self) -> tuple[StageResult, ...]:
        return (self.collection, self.main_analysis, self.counter_analysis, self.final_report)


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    draft: AnalysisDraft
    approved_by: str
    status: EditorialStatus = EditorialStatus.APPROVED


class AnalysisPipeline:
    def __init__(
        self,
        config: AnalysisConfig,
        providers: ProviderRegistry,
        prompts: PromptLibrary | None = None,
        run_store: RunStore | None = None,
    ):
        self.config = config
        self.providers = providers
        self.prompts = prompts or PromptLibrary()
        self.run_store = run_store

    def _run(self, role: AnalysisRole, context: str, run_id: str | None, position: int) -> StageResult:
        started = datetime.now(timezone.utc)
        configured = self.config.for_role(role)
        prompt = None

        def fail(message: str) -> None:
            if self.run_store and run_id:
                self.run_store.record_stage(
                    run_id, position=position, role=role.value, provider=configured.provider,
                    requested_model=configured.model,
                    prompt_version=prompt.version if prompt else configured.prompt_version,
                    prompt_sha256=prompt.sha256 if prompt else None,
                    input_text=context, started_at=started, error=message,
                )

        try:
            provider, role_config = self.providers.for_role(self.config, role)
            prompt = self.prompts.load(role, role_config.prompt_version)
        except (ProviderError, PromptError) as exc:
            fail(str(exc))
            raise
        request = ProviderRequest(
            role=role,
            prompt=prompt.text,
            context=context,
            model=role_config.model,
            max_output_tokens=role_config.max_output_tokens,
        )
        try:
            response = provider.complete(request)
        except ProviderError as exc:
            fail(str(exc))
            raise
        except Exception as exc:
            # Avoid leaking vendor exception text that may contain request data or credentials.
            fail(f"Provider failed during role '{role.value}' ({type(exc).__name__}).")
            raise ProviderError(f"Provider failed during role '{role.value}'.") from exc
        if not isinstance(response, ProviderResponse) or not response.text.strip():
            fail("Provider returned an empty or invalid response.")
            raise ProviderError(f"Provider returned an empty or invalid response for role '{role.value}'.")
        cost = estimate_cost(response, role_config)
        if self.run_store and run_id:
            self.run_store.record_stage(
                run_id, position=position, role=role.value, provider=configured.provider,
                requested_model=role_config.model, prompt_version=prompt.version, prompt_sha256=prompt.sha256,
                input_text=context, started_at=started, response=response, estimated_cost=cost,
            )
        return StageResult(
            role=role,
            response=response,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            estimated_cost=cost,
        )

    def analyze(
        self, source_material: str, *, source_map: Mapping[str, SourceRecord] | None = None
    ) -> AnalysisDraft:
        """Run all four stages.

        ``source_map`` (id -> stored record, as returned by ``format_sources_for_prompt``) is
        recorded in the run log so the exact sources behind a draft can be recovered.
        """
        if not source_material.strip():
            raise ValueError("Source material must not be empty")

        run_id = None
        if self.run_store:
            ids = {sid: rec.id for sid, rec in (source_map or {}).items() if rec.id is not None}
            run_id = self.run_store.start_run(self.config, source_material, ids)
        try:
            collection = self._run(AnalysisRole.COLLECTION, source_material, run_id, 1)
            main = self._run(AnalysisRole.MAIN_ANALYSIS, collection.response.text, run_id, 2)
            counter = self._run(
                AnalysisRole.COUNTER_ANALYSIS,
                f"Source material:\n{collection.response.text}\n\nMain analysis to review:\n{main.response.text}",
                run_id, 3,
            )
            final = self._run(
                AnalysisRole.FINAL_REPORT,
                f"Main analysis:\n{main.response.text}\n\nIndependent counter-analysis:\n{counter.response.text}",
                run_id, 4,
            )
        except Exception as exc:
            if self.run_store and run_id:
                # ProviderError/PromptError text is already free of request data; anything else is reduced to its type.
                safe = str(exc) if isinstance(exc, (ProviderError, PromptError)) else f"Unexpected {type(exc).__name__}"
                self.run_store.finish_run(run_id, error=safe)
            raise
        warnings = self._independence_warnings(main, counter)
        if self.run_store and run_id:
            self.run_store.finish_run(run_id, report=final.response.text, warnings=warnings)
        return AnalysisDraft(
            report=final.response.text,
            collection=collection,
            main_analysis=main,
            counter_analysis=counter,
            final_report=final,
            warnings=warnings,
            run_id=run_id,
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
