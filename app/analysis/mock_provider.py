"""Deterministic provider for local development and tests."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from .providers import AnalysisRole, ProviderError, ProviderRequest, ProviderResponse


EMPTY_MAIN_JSON = (
    '{"claims": [], "article5": {"category": "no_basis", "reasoning": "Inget underlag i mockmaterialet.", '
    '"sources": []}, "hypotheses": [], "indicators": {"escalation": [], "deescalation": []}}'
)
EMPTY_COUNTER_JSON = '{"objections": [], "section_verdicts": [], "disagreements": []}'


def with_block(text: str, json_text: str) -> str:
    """Append the machine-readable block the structured stages require."""
    return f"{text}\n\n```json\n{json_text}\n```"


_DEFAULT_BLOCKS = {AnalysisRole.MAIN_ANALYSIS: EMPTY_MAIN_JSON, AnalysisRole.COUNTER_ANALYSIS: EMPTY_COUNTER_JSON}


class MockProvider:
    def __init__(self, responses: Iterable[str] | None = None, *, fail_on_call: int | None = None):
        self._responses = deque(responses or ())
        self.fail_on_call = fail_on_call
        self.calls: list[ProviderRequest] = []
        self._role_counts: dict[AnalysisRole, int] = defaultdict(int)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        self._role_counts[request.role] += 1
        if self.fail_on_call == len(self.calls):
            raise ProviderError(f"Mock failure on call {len(self.calls)}")
        if self._responses:
            text = self._responses.popleft()
        else:
            text = f"Mock {request.role.value} draft based on supplied context."
            if request.role in _DEFAULT_BLOCKS:
                text = with_block(text, _DEFAULT_BLOCKS[request.role])
        return ProviderResponse(text=text, provider="mock", model=request.model)
