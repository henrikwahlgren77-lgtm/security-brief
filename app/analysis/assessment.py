"""Structured assessments and the rules that keep them honest.

Models write prose plus a machine-readable JSON block. This module parses and
validates that block strictly, then applies deterministic rules so that the
code, not a model, has the last word on:

- how well a claim is supported (a "confirmed" claim needs a primary source or
  at least two independent origins; dependent sources count once);
- whether something is really new (old dates and already-used sources are not);
- whether disagreement from the independent review survives into the draft.

Nothing here calls a model or the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .sources import OriginType, SourceRecord, canonicalize_url, independent_origins


class AssessmentError(RuntimeError):
    """Model output could not be turned into a valid structured assessment."""


class Classification(StrEnum):
    CONFIRMED = "confirmed"
    CREDIBLE = "credible_unconfirmed"
    DISPUTED = "disputed"
    UNCONFIRMED = "unconfirmed"


class ClaimKind(StrEnum):
    FACT = "fact"
    OFFICIAL = "official_assessment"
    MEDIA = "media_report"
    ANALYSIS = "own_analysis"


class Change(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    CONTINUING = "continuing"
    CONFIRMATION = "confirmation"
    DENIAL = "denial"
    BACKGROUND = "background"
    NO_CHANGE = "no_change"
    NO_COMPARISON = "no_comparison"


class Article5Category(StrEnum):
    ATTACK = "attack"  # direct military attack on a NATO country
    SERIOUS_INCIDENT = "serious_incident"  # could lead to military escalation
    HYBRID = "hybrid_activity"
    RHETORIC = "rhetoric"  # statements or threats without concrete military action
    NO_BASIS = "no_basis"


class Severity(StrEnum):
    DECISIVE = "decisive"
    SUBSTANTIAL = "substantial"
    MINOR = "minor"


class ObjectionType(StrEnum):
    UNSUPPORTED = "unsupported"
    MISCLASSIFIED = "misclassified"
    CONFLATION = "conflation"
    TIME = "time"
    ALTERNATIVE = "alternative"
    OVERSTATED = "overstated"
    UNDERSTATED = "understated"
    CONTRADICTION = "contradiction"
    MISSING = "missing"
    ARTICLE5 = "article5"
    NUMERIC_RISK = "numeric_risk"


class Verdict(StrEnum):
    HOLDS = "holds"
    PARTLY = "partly"
    FAILS = "fails"


_SOURCE_ID = re.compile(r"^S\d{1,3}$")
_PREVIOUS_ID = re.compile(r"^P\d{1,3}$")
_CLAIM_ID = re.compile(r"^C\d{1,3}$")
_OBJECTION_ID = re.compile(r"^O\d{1,3}$")
_NUMERIC_RISK = re.compile(r"\d{1,3}(?:[.,]\d+)?\s?(?:%|procent)", re.IGNORECASE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _ids(values: list[str], pattern: re.Pattern[str], what: str) -> list[str]:
    for value in values:
        if not pattern.match(value):
            raise ValueError(f"invalid {what} id")
    return values


class Claim(_Strict):
    id: str
    text: str = Field(min_length=1, max_length=600)
    kind: ClaimKind
    attributed_to: str | None = Field(default=None, max_length=120)
    classification: Classification
    sources: list[str] = Field(default_factory=list, max_length=10)
    change: Change
    previous_refs: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("id")
    @classmethod
    def _claim_id(cls, value: str) -> str:
        if not _CLAIM_ID.match(value):
            raise ValueError("invalid claim id")
        return value

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: list[str]) -> list[str]:
        return _ids(value, _SOURCE_ID, "source")

    @field_validator("previous_refs")
    @classmethod
    def _refs(cls, value: list[str]) -> list[str]:
        return _ids(value, _PREVIOUS_ID, "previous")


class Explanation(_Strict):
    text: str = Field(min_length=1, max_length=400)
    supports: list[str] = Field(default_factory=list, max_length=6)
    against: list[str] = Field(default_factory=list, max_length=6)
    missing: list[str] = Field(default_factory=list, max_length=6)


class Hypothesis(_Strict):
    event: str = Field(min_length=1, max_length=300)
    explanations: list[Explanation] = Field(min_length=2, max_length=5)
    strongest: int

    @model_validator(mode="after")
    def _strongest_exists(self) -> "Hypothesis":
        if not 0 <= self.strongest < len(self.explanations):
            raise ValueError("strongest must index an explanation")
        return self


class Article5(_Strict):
    category: Article5Category
    reasoning: str = Field(min_length=1, max_length=800)
    sources: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: list[str]) -> list[str]:
        return _ids(value, _SOURCE_ID, "source")


class Indicators(_Strict):
    escalation: list[str] = Field(default_factory=list, max_length=8)
    deescalation: list[str] = Field(default_factory=list, max_length=8)


class MainAssessment(_Strict):
    claims: list[Claim] = Field(max_length=40)
    article5: Article5
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=10)
    indicators: Indicators = Field(default_factory=Indicators)

    @model_validator(mode="after")
    def _unique_claim_ids(self) -> "MainAssessment":
        ids = [c.id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate claim ids")
        return self


class Objection(_Strict):
    id: str
    target: str = Field(min_length=1, max_length=60)
    type: ObjectionType
    severity: Severity
    text: str = Field(min_length=1, max_length=600)

    @field_validator("id")
    @classmethod
    def _objection_id(cls, value: str) -> str:
        if not _OBJECTION_ID.match(value):
            raise ValueError("invalid objection id")
        return value


class SectionVerdict(_Strict):
    section: str = Field(min_length=1, max_length=80)
    verdict: Verdict


class CounterAssessment(_Strict):
    objections: list[Objection] = Field(max_length=40)
    section_verdicts: list[SectionVerdict] = Field(default_factory=list, max_length=20)
    disagreements: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _unique_objection_ids(self) -> "CounterAssessment":
        ids = [o.id for o in self.objections]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate objection ids")
        return self


# --- parsing --------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _safe_validation_summary(exc: ValidationError) -> str:
    """Field paths and error types only: pydantic's default text echoes model output."""
    parts = [f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['type']}" for e in exc.errors()[:8]]
    return "; ".join(parts)


def split_structured(text: str) -> tuple[str, dict[str, Any]]:
    """Return (prose without the block, parsed JSON) using the last ```json block."""
    matches = list(_JSON_BLOCK.finditer(text))
    if not matches:
        raise AssessmentError("no ```json block found in the answer")
    match = matches[-1]
    try:
        data = json.loads(match.group(1))
    except ValueError:
        raise AssessmentError("the ```json block is not valid JSON") from None
    prose = (text[: match.start()] + text[match.end():]).strip()
    return prose, data


def _parse(text: str, model: type[BaseModel]):
    prose, data = split_structured(text)
    try:
        return prose, model.model_validate(data)
    except ValidationError as exc:
        raise AssessmentError(f"the JSON block does not match the schema ({_safe_validation_summary(exc)})") from None


def parse_main(text: str) -> tuple[str, MainAssessment]:
    return _parse(text, MainAssessment)


def parse_counter(text: str) -> tuple[str, CounterAssessment]:
    return _parse(text, CounterAssessment)


# --- previous report ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PreviousClaim:
    id: str  # P1, P2 ... as shown to the model
    text: str
    classification: str
    source_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreviousContext:
    claims: Mapping[str, PreviousClaim]
    run_started_at: datetime | None = None

    @property
    def urls(self) -> frozenset[str]:
        return frozenset(url for claim in self.claims.values() for url in claim.source_urls)


def format_previous(previous: PreviousContext | None) -> str:
    if previous is None or not previous.claims:
        return "Föregående rapport: ingen tillgänglig. Använd förändringstypen no_comparison, inte new."
    when = previous.run_started_at.date().isoformat() if previous.run_started_at else "okänt datum"
    lines = [f"Föregående rapport (körning {when}), kalibrerade påståenden:"]
    lines += [f"[{c.id}] ({c.classification}) {c.text}" for c in previous.claims.values()]
    return "\n".join(lines)


# --- calibration ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    subject: str | None
    message: str
    level: str = "warn"  # "warn" needs the editor's attention, "info" is context

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}{' ' + self.subject if self.subject else ''}: {self.message}"


@dataclass(frozen=True, slots=True)
class CalibratedClaim:
    claim: Claim
    source_urls: tuple[str, ...]
    downgraded_from: Classification | None = None
    changed_from: Change | None = None


@dataclass(frozen=True, slots=True)
class CalibratedAssessment:
    assessment: MainAssessment
    claims: tuple[CalibratedClaim, ...]
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    def render(self) -> str:
        """Text for later stages. These classifications override the model's own."""
        if not self.claims:
            return "(inga påståenden)"
        lines = []
        for item in self.claims:
            c = item.claim
            notes = []
            if item.downgraded_from:
                notes.append(f"nedgraderad från {item.downgraded_from.value} av systemet")
            if item.changed_from:
                notes.append(f"förändringstyp rättad från {item.changed_from.value}")
            lines.append(
                f"{c.id} | {c.classification.value} | {c.kind.value} | {c.change.value} | "
                f"{c.attributed_to or '-'} | {','.join(c.sources) or 'ingen källa'} | {c.text}"
                + (f"  ({'; '.join(notes)})" if notes else "")
            )
        a5 = self.assessment.article5
        lines.append(f"Artikel 5-kategori: {a5.category.value}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        data = self.assessment.model_dump(mode="json")
        for entry, item in zip(data["claims"], self.claims):
            entry["source_urls"] = list(item.source_urls)
            entry["downgraded_from"] = item.downgraded_from.value if item.downgraded_from else None
            entry["changed_from"] = item.changed_from.value if item.changed_from else None
        return data


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3}


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _known_dates(records: Sequence[SourceRecord]) -> list[datetime]:
    dates: list[datetime] = []
    for record in records:
        if record.published_at:
            try:
                dates.append(datetime.fromisoformat(record.published_at).astimezone(timezone.utc))
            except ValueError:
                pass
        if record.event_date:
            try:
                dates.append(datetime.combine(date.fromisoformat(record.event_date[:10]), datetime.min.time(), timezone.utc))
            except ValueError:
                pass
    return dates


REPEAT_SIMILARITY = 0.8
_REFERENCING = {Change.CHANGED, Change.CONTINUING, Change.CONFIRMATION, Change.DENIAL, Change.NO_CHANGE}


def calibrate(
    main: MainAssessment,
    id_map: Mapping[str, SourceRecord],
    *,
    previous: PreviousContext | None = None,
    window_start: datetime | None = None,
) -> CalibratedAssessment:
    """Apply the evidence, time and change rules to a validated assessment."""
    if window_start is not None and window_start.tzinfo is None:
        raise ValueError("window_start must be timezone-aware")
    issues: list[Issue] = []
    calibrated: list[CalibratedClaim] = []
    has_previous = previous is not None and bool(previous.claims)

    for claim in main.claims:
        known = [s for s in claim.sources if s in id_map]
        unknown = [s for s in claim.sources if s not in id_map]
        if unknown:
            issues.append(Issue("unknown_source", claim.id, f"cites source ids that were never provided: {', '.join(unknown)}"))
        records = [id_map[s] for s in known]
        urls = tuple(sorted({canonicalize_url(r.url) for r in records}))
        groups = independent_origins(records)
        has_primary = any(r.tier == 1 and r.origin_type is OriginType.ORIGINAL for r in records)

        classification, downgraded = claim.classification, None

        def downgrade(to: Classification, code: str, message: str) -> None:
            nonlocal classification, downgraded
            issues.append(Issue(code, claim.id, message))
            downgraded = downgraded or classification
            classification = to

        if not records and claim.kind is not ClaimKind.ANALYSIS:
            if classification is not Classification.UNCONFIRMED:
                downgrade(Classification.UNCONFIRMED, "no_source", "a factual claim without any provided source cannot be presented as supported")
            else:
                issues.append(Issue("no_source", claim.id, "claim has no provided source"))
        if classification is Classification.CONFIRMED:
            if claim.kind is ClaimKind.ANALYSIS:
                downgrade(Classification.CREDIBLE, "analysis_not_fact", "own analysis is never 'confirmed'")
            elif not has_primary and len(groups) < 2:
                downgrade(
                    Classification.CREDIBLE, "insufficient_verification",
                    f"'confirmed' needs a primary source or two independent origins; found {len(groups)} independent origin(s), no primary",
                )

        change, changed_from = claim.change, None

        def retype(to: Change, code: str, message: str, level: str = "warn") -> None:
            nonlocal change, changed_from
            issues.append(Issue(code, claim.id, message, level))
            changed_from = changed_from or change
            change = to

        if not has_previous:
            if change not in (Change.BACKGROUND, Change.NO_COMPARISON):
                retype(Change.NO_COMPARISON, "no_previous_report", "there is no earlier report to compare with", "info")
        else:
            if change in _REFERENCING and not [r for r in claim.previous_refs if r in previous.claims]:
                retype(Change.NO_COMPARISON, "missing_previous_ref", f"'{change.value}' must reference an existing earlier claim (P#)")
            if change is Change.NEW:
                if urls and set(urls) <= previous.urls:
                    retype(Change.CONTINUING, "repeat_sources", "every cited source was already used in the previous report")
                elif any(_similar(claim.text, p.text) >= REPEAT_SIMILARITY for p in previous.claims.values()):
                    retype(Change.CONTINUING, "repeat_text", "wording is nearly identical to a claim in the previous report")
        if change is Change.NEW and window_start is not None:
            dates = _known_dates(records)
            if dates and max(dates) < window_start:
                retype(Change.BACKGROUND, "old_presented_as_new", "all dated sources are older than the reporting window")
            elif not dates:
                issues.append(Issue("date_unknown", claim.id, "no source states a date, so 'new' cannot be verified", "info"))

        adjusted = claim.model_copy(update={"classification": classification, "change": change})
        calibrated.append(CalibratedClaim(adjusted, urls, downgraded, changed_from))

    article5 = main.article5
    a5_records = [id_map[s] for s in article5.sources if s in id_map]
    if article5.category is not Article5Category.NO_BASIS and not a5_records:
        issues.append(Issue("article5_no_source", "article5", f"category '{article5.category.value}' without any provided source; reset to no_basis"))
        article5 = article5.model_copy(update={"category": Article5Category.NO_BASIS})
    elif article5.category is Article5Category.ATTACK and not any(r.tier == 1 for r in a5_records):
        issues.append(Issue("article5_attack_no_primary", "article5", "'attack' is claimed without a primary (tier 1) source"))

    free_text = {
        "article5": [article5.reasoning],
        "indicators": main.indicators.escalation + main.indicators.deescalation,
        "hypotheses": [t for h in main.hypotheses for e in h.explanations for t in [e.text, *e.supports, *e.against]],
    }
    for where, texts in free_text.items():
        if any(_NUMERIC_RISK.search(t) for t in texts):
            issues.append(Issue("numeric_risk", where, "contains a percentage or numeric risk figure; no documented method exists for these"))

    adjusted_main = main.model_copy(update={"claims": [c.claim for c in calibrated], "article5": article5})
    return CalibratedAssessment(adjusted_main, tuple(calibrated), tuple(issues))


# --- disagreement ---------------------------------------------------------------

_SEVERITY_SV = {Severity.DECISIVE: "Avgörande", Severity.SUBSTANTIAL: "Väsentlig", Severity.MINOR: "Mindre"}
_UNCERTAINTY_HEADING = re.compile(r"^##\s+Osäkerheter och luckor\s*$", re.MULTILINE)
_ANY_H2 = re.compile(r"^##\s+", re.MULTILINE)
_SOURCES_HEADING = re.compile(r"^##\s+Källor\s*$", re.MULTILINE)


def check_counter(counter: CounterAssessment, calibrated: CalibratedAssessment) -> list[Issue]:
    claim_ids = {c.claim.id for c in calibrated.claims}
    return [
        Issue("unknown_target", o.id, f"objection targets '{o.target}', which is not a claim id", "info")
        for o in counter.objections
        if _CLAIM_ID.match(o.target) and o.target not in claim_ids
    ]


def unresolved(objections: Sequence[Objection]) -> list[Objection]:
    """Objections that must reach the editor: only a human may dismiss them."""
    return [o for o in objections if o.severity in (Severity.DECISIVE, Severity.SUBSTANTIAL)]


def ensure_disagreement_reported(report: str, objections: Sequence[Objection]) -> tuple[str, list[str]]:
    """Make sure every decisive/substantial objection appears under 'Osäkerheter och luckor'.

    The writer is told to cite objections by id (O3). Any that are missing are
    appended by the system, so disagreement cannot be dropped by omission.
    Returns the report and the ids that had to be added.
    """
    required = unresolved(objections)
    if not required:
        return report, []
    heading = _UNCERTAINTY_HEADING.search(report)
    section_end = len(report)
    section = ""
    if heading:
        following = _ANY_H2.search(report, heading.end())
        section_end = following.start() if following else len(report)
        section = report[heading.end():section_end]
    missing = [o for o in required if not re.search(rf"\b{re.escape(o.id)}\b", section)]
    if not missing:
        return report, []
    bullets = "\n".join(
        f"- Olöst invändning från den oberoende granskningen {o.id} ({_SEVERITY_SV[o.severity]}, avser {o.target}): "
        f"{o.text} _(tillagd av systemet)_"
        for o in missing
    )
    if heading:
        head, tail = report[:section_end].rstrip("\n"), report[section_end:]
        return f"{head}\n{bullets}\n\n{tail}".rstrip("\n") + "\n", [o.id for o in missing]
    block = f"## Osäkerheter och luckor\n\n{bullets}\n\n"
    sources = _SOURCES_HEADING.search(report)
    if sources:
        return report[: sources.start()] + block + report[sources.start():], [o.id for o in missing]
    return report.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n", [o.id for o in missing]


def render_objections(objections: Sequence[Objection]) -> str:
    if not objections:
        return "(inga invändningar)"
    return "\n".join(f"{o.id} | {o.severity.value} | {o.type.value} | {o.target} | {o.text}" for o in objections)
