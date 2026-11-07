"""Model-independent analysis workflow."""

from .pipeline import AnalysisDraft, AnalysisPipeline, EditorialStatus, approve_for_publication
from .adapters import AnthropicAdapter, MistralAdapter, OpenAIAdapter, build_registry
from .allowlist import Allowlist
from .context import expand_citations, format_sources_for_prompt
from .fetcher import SourceFetcher
from .sources import (
    FetchStatus,
    OriginType,
    SourceError,
    SourceRecord,
    SourceStore,
    SourceTopic,
    find_unfetched_urls,
    independent_origins,
)
from .prompts import Prompt, PromptError, PromptLibrary
from .providers import (
    AnalysisConfig,
    AnalysisRole,
    ProviderError,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
    RoleConfig,
    estimate_cost,
)

__all__ = [
    "AnthropicAdapter",
    "MistralAdapter",
    "OpenAIAdapter",
    "Prompt",
    "PromptError",
    "PromptLibrary",
    "build_registry",
    "estimate_cost",
    "expand_citations",
    "format_sources_for_prompt",
    "Allowlist",
    "FetchStatus",
    "OriginType",
    "SourceError",
    "SourceFetcher",
    "SourceRecord",
    "SourceStore",
    "SourceTopic",
    "find_unfetched_urls",
    "independent_origins",
    "AnalysisDraft",
    "AnalysisConfig",
    "AnalysisPipeline",
    "AnalysisRole",
    "EditorialStatus",
    "ProviderError",
    "ProviderRegistry",
    "ProviderRequest",
    "ProviderResponse",
    "RoleConfig",
    "approve_for_publication",
]
