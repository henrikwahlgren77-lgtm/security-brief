"""Four-stage analysis workflow with an explicit human editorial gate.

Stages: collection -> main analysis -> independent counter-analysis -> final
report. The main and counter stages must also return a machine-readable block;
code validates it (see ``assessment``) and applies deterministic rules, so a
model cannot grant itself "confirmed", call old news new, or make the review's
objections disappear.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable, Mapping

from .assessment import (
    AssessmentError,
    CalibratedAssessment,
    CounterAssessment,
    Issue,
    PreviousContext,
    calibrate,
    check_counter,
    ensure_disagreement_reported,
    format_previous,
    parse_counter,
    parse_main,
    render_objections,
)
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

REPORTING_WINDOW = timedelta(hours=24)
_NUMERIC_RISK = re.compile(r"\d{1,3}(?:[.,]\d+)?\s?(?:%|procent)", re.IGNORECASE)


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
    assessment: CalibratedAssessment | None = None
    review: CounterAssessment | None = None
    added_objections: tuple[str, ...] = ()  # ids the system had to add to the report

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

    # -- one model call ------------------------------------------------------
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

    def _run_structured(
        self, role: AnalysisRole, context: str, run_id: str | None, positions: "itertools.count[int]",
        parser: Callable[[str], tuple[str, object]],
    ) -> tuple[StageResult, str, object, bool]:
        """Run a stage whose answer must carry a valid JSON block; one corrective retry.

        Returns (stage, prose, parsed, retried).
        """
        result = self._run(role, context, run_id, next(positions))
        try:
            prose, parsed = parser(result.response.text)
            return result, prose, parsed, False
        except AssessmentError as first:
            retry_context = (
                f"{context}\n\n[Systemmeddelande] Ditt förra svar kunde inte tolkas: {first}. "
                "Svara igen med hela analysen och ett giltigt JSON-block enligt instruktionen."
            )
            retry = self._run(role, retry_context, run_id, next(positions))
            try:
                prose, parsed = parser(retry.response.text)
            except AssessmentError as second:
                raise AssessmentError(f"{role.value}: structured output was invalid twice ({second})") from None
            return retry, prose, parsed, True

    # -- the whole analysis --------------------------------------------------
    def analyze(
        self,
        source_material: str,
        *,
        source_map: Mapping[str, SourceRecord] | None = None,
        previous: PreviousContext | None = None,
        use_history: bool = True,
        window_start: datetime | None = None,
    ) -> AnalysisDraft:
        """Run all four stages.

        ``source_map`` (id -> stored record, as returned by ``format_sources_for_prompt``) is
        what claims are checked against and what the run log records. ``previous`` defaults to
        the claims of the last completed run in the run log (disable with ``use_history=False``).
        ``window_start`` defaults to 24 hours ago.
        """
        if not source_material.strip():
            raise ValueError("Source material must not be empty")
        id_map = dict(source_map or {})
        window_start = window_start or (datetime.now(timezone.utc) - REPORTING_WINDOW)
        if previous is None and use_history and self.run_store:
            previous = self.run_store.latest_previous()

        run_id = None
        if self.run_store:
            ids = {sid: rec.id for sid, rec in id_map.items() if rec.id is not None}
            run_id = self.run_store.start_run(self.config, source_material, ids)
        positions = itertools.count(1)
        notes: list[str] = []
        try:
            collection = self._run(AnalysisRole.COLLECTION, source_material, run_id, next(positions))

            main_context = f"{collection.response.text}\n\n{format_previous(previous)}"
            main, main_prose, main_parsed, retried = self._run_structured(
                AnalysisRole.MAIN_ANALYSIS, main_context, run_id, positions, parse_main)
            if retried:
                notes.append("The main analysis needed a format correction before its structured block was valid.")
            calibrated = calibrate(main_parsed, id_map, previous=previous, window_start=window_start)
            checked = calibrated.render()

            counter_context = (
                f"Source material:\n{collection.response.text}\n\n"
                f"Main analysis to review:\n{main_prose}\n\n"
                f"Checked claim list (system calibration, binding):\n{checked}"
            )
            counter, counter_prose, counter_parsed, retried = self._run_structured(
                AnalysisRole.COUNTER_ANALYSIS, counter_context, run_id, positions, parse_counter)
            if retried:
                notes.append("The counter-analysis needed a format correction before its structured block was valid.")
            review_issues = check_counter(counter_parsed, calibrated)

            final_context = (
                f"Main analysis:\n{main_prose}\n\n"
                f"Checked claim list (system calibration, binding):\n{checked}\n\n"
                f"Independent counter-analysis:\n{counter_prose}\n\n"
                f"Objections (report these by id):\n{render_objections(counter_parsed.objections)}"
            )
            final = self._run(AnalysisRole.FINAL_REPORT, final_context, run_id, next(positions))
        except Exception as exc:
            if self.run_store and run_id:
                # These errors are built by us and contain no request data; anything else is reduced to its type.
                safe = str(exc) if isinstance(exc, (ProviderError, PromptError, AssessmentError)) else f"Unexpected {type(exc).__name__}"
                self.run_store.finish_run(run_id, error=safe)
            raise

        report, added = ensure_disagreement_reported(final.response.text, counter_parsed.objections)
        issues: list[Issue] = [*calibrated.issues, *review_issues]
        if added:
            issues.append(Issue("disagreement_added", None, f"objections missing from the report were added by the system: {', '.join(added)}"))
        if _NUMERIC_RISK.search(report):
            issues.append(Issue("numeric_risk", "report", "the report contains a percentage; check that it is not a risk figure"))
        warnings = (
            *self._independence_warnings(main, counter),
            *notes,
            *(str(i) for i in issues if i.level == "warn"),
        )
        if self.run_store and run_id:
            stored = calibrated.to_dict()
            stored["objections"] = counter_parsed.model_dump(mode="json")["objections"]
            stored["section_verdicts"] = counter_parsed.model_dump(mode="json")["section_verdicts"]
            stored["disagreements"] = counter_parsed.disagreements
            stored["issues"] = [{"code": i.code, "subject": i.subject, "message": i.message, "level": i.level} for i in issues]
            stored["added_objections"] = added
            self.run_store.save_assessment(run_id, stored)
            self.run_store.finish_run(run_id, report=report, warnings=warnings)
        return AnalysisDraft(
            report=report,
            collection=collection,
            main_analysis=main,
            counter_analysis=counter,
            final_report=final,
            warnings=warnings,
            run_id=run_id,
            assessment=calibrated,
            review=counter_parsed,
            added_objections=tuple(added),
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
