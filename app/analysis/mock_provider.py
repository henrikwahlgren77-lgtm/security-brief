"""Deterministic provider for local development and tests."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from .providers import AnalysisRole, ProviderError, ProviderRequest, ProviderResponse


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
        return ProviderResponse(text=text, provider="mock", model=request.model)
