"""Shared provider contract and environment based role configuration.

Provider adapters should translate this neutral request/response contract to a
vendor API. Credentials are intentionally not part of either the config or the
request; concrete adapters must read credentials from their process environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol


class AnalysisRole(StrEnum):
    COLLECTION = "collection"
    MAIN_ANALYSIS = "main_analysis"
    COUNTER_ANALYSIS = "counter_analysis"
    FINAL_REPORT = "final_report"


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    role: AnalysisRole
    prompt: str
    context: str
    model: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    provider: str
    model: str | None = None


class AnalysisProvider(Protocol):
    """Smallest common operation required from any provider adapter."""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return generated text or raise ProviderError on an adapter failure."""


class ProviderError(RuntimeError):
    """A provider could not complete a request."""


@dataclass(frozen=True, slots=True)
class RoleConfig:
    provider: str
    model: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    roles: Mapping[AnalysisRole, RoleConfig]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AnalysisConfig":
        values = os.environ if env is None else env
        roles = {}
        for role in AnalysisRole:
            prefix = f"ANALYSIS_{role.value.upper()}"
            provider = values.get(f"{prefix}_PROVIDER", "mock").strip().lower()
            model = values.get(f"{prefix}_MODEL", "").strip() or None
            if not provider:
                raise ValueError(f"{prefix}_PROVIDER must not be empty")
            roles[role] = RoleConfig(provider=provider, model=model)
        return cls(roles=roles)

    def for_role(self, role: AnalysisRole) -> RoleConfig:
        try:
            return self.roles[role]
        except KeyError as exc:
            raise ValueError(f"No model configuration for role: {role.value}") from exc


class ProviderRegistry:
    """Maps configured provider identifiers to adapter instances."""

    def __init__(self, providers: Mapping[str, AnalysisProvider]):
        self._providers = {name.strip().lower(): value for name, value in providers.items()}

    def for_role(self, config: AnalysisConfig, role: AnalysisRole) -> tuple[AnalysisProvider, RoleConfig]:
        role_config = config.for_role(role)
        try:
            provider = self._providers[role_config.provider]
        except KeyError as exc:
            available = ", ".join(sorted(self._providers)) or "none"
            raise ProviderError(
                f"Provider '{role_config.provider}' is not registered for role '{role.value}'. "
                f"Registered providers: {available}."
            ) from exc
        return provider, role_config
