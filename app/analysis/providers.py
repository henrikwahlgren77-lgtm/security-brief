"""Shared provider contract and environment based role configuration.

Provider adapters translate this neutral request/response contract to a vendor
API. Credentials are intentionally not part of either the config or the
request; concrete adapters read credentials from their process environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol

DEFAULT_MAX_OUTPUT_TOKENS = 4096
MAX_ALLOWED_OUTPUT_TOKENS = 64_000


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
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    provider: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    attempts: int = 1
    finish_reason: str | None = None


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
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    prompt_version: str | None = None  # None = highest available version
    # Optional operator-supplied prices (currency per million tokens). The code
    # contains no vendor prices; without them cost is reported as unknown.
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None


def _optional_float(values: Mapping[str, str], key: str) -> float | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if number < 0 or number != number:
        raise ValueError(f"{key} must not be negative")
    return number


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
            raw_tokens = values.get(f"{prefix}_MAX_OUTPUT_TOKENS", "").strip()
            try:
                max_tokens = int(raw_tokens) if raw_tokens else DEFAULT_MAX_OUTPUT_TOKENS
            except ValueError as exc:
                raise ValueError(f"{prefix}_MAX_OUTPUT_TOKENS must be an integer") from exc
            if not 1 <= max_tokens <= MAX_ALLOWED_OUTPUT_TOKENS:
                raise ValueError(f"{prefix}_MAX_OUTPUT_TOKENS must be between 1 and {MAX_ALLOWED_OUTPUT_TOKENS}")
            price_in = _optional_float(values, f"{prefix}_PRICE_INPUT_PER_MTOK")
            price_out = _optional_float(values, f"{prefix}_PRICE_OUTPUT_PER_MTOK")
            if (price_in is None) != (price_out is None):
                raise ValueError(f"{prefix}: set both price variables or neither")
            roles[role] = RoleConfig(
                provider=provider,
                model=model,
                max_output_tokens=max_tokens,
                prompt_version=values.get(f"{prefix}_PROMPT_VERSION", "").strip() or None,
                price_input_per_mtok=price_in,
                price_output_per_mtok=price_out,
            )
        return cls(roles=roles)

    def for_role(self, role: AnalysisRole) -> RoleConfig:
        try:
            return self.roles[role]
        except KeyError as exc:
            raise ValueError(f"No model configuration for role: {role.value}") from exc


def estimate_cost(response: ProviderResponse, role_config: RoleConfig) -> float | None:
    """Cost from operator-supplied prices, or None when prices or token counts are unknown."""
    if (
        role_config.price_input_per_mtok is None
        or role_config.price_output_per_mtok is None
        or response.input_tokens is None
        or response.output_tokens is None
    ):
        return None
    return (
        response.input_tokens * role_config.price_input_per_mtok
        + response.output_tokens * role_config.price_output_per_mtok
    ) / 1_000_000


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
