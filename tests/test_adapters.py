import json

import httpx
import pytest

from app.analysis import AnalysisConfig, AnalysisPipeline, AnalysisRole, ProviderError, ProviderRequest
from app.analysis.adapters import (
    AnthropicAdapter,
    MistralAdapter,
    OpenAIAdapter,
    build_registry,
)
from app.analysis.prompts import PromptLibrary

KEY = "sk-test-SECRET-KEY-123"
ENV = {"OPENAI_API_KEY": KEY, "ANTHROPIC_API_KEY": KEY, "MISTRAL_API_KEY": KEY}


def chat_body(text="Hej", finish="stop", model="served-model"):
    return {"model": model, "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def anthropic_body(text="Hej", stop="end_turn", model="served-model"):
    return {"model": model, "stop_reason": stop, "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


ADAPTERS = [
    pytest.param(OpenAIAdapter, chat_body, "https://api.openai.com/v1/chat/completions", id="openai"),
    pytest.param(MistralAdapter, chat_body, "https://api.mistral.ai/v1/chat/completions", id="mistral"),
    pytest.param(AnthropicAdapter, anthropic_body, "https://api.anthropic.com/v1/messages", id="anthropic"),
]

REQUEST = ProviderRequest(role=AnalysisRole.MAIN_ANALYSIS, prompt="SYSTEM PROMPT", context="THE CONTEXT",
                          model="configured-model", max_output_tokens=777)


class Server:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses[min(len(self.requests), len(self.responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def make(adapter_cls, *responses, env=ENV):
    server, sleeps = Server(*responses), []
    adapter = adapter_cls(env, transport=httpx.MockTransport(server), sleep=sleeps.append)
    return adapter, server, sleeps


def ok(body):
    return httpx.Response(200, json=body)


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_request_is_sent_to_the_fixed_vendor_url_with_prompt_context_and_limits(adapter_cls, body, url):
    adapter, server, _ = make(adapter_cls, ok(body()))
    response = adapter.complete(REQUEST)
    sent = server.requests[0]
    payload = json.loads(sent.content)
    assert str(sent.url) == url
    assert payload["model"] == "configured-model"
    assert "SYSTEM PROMPT" in json.dumps(payload) and "THE CONTEXT" in json.dumps(payload)
    assert 777 in payload.values()
    assert KEY in (sent.headers.get("authorization", "") + sent.headers.get("x-api-key", ""))
    assert (response.text, response.provider, response.model) == ("Hej", adapter.name, "served-model")
    assert (response.input_tokens, response.output_tokens, response.attempts) == (10, 5, 1)
    assert response.latency_ms is not None and response.finish_reason in ("stop", "end_turn")


def test_anthropic_headers_and_system_prompt_placement():
    adapter, server, _ = make(AnthropicAdapter, ok(anthropic_body()))
    adapter.complete(REQUEST)
    sent = server.requests[0]
    payload = json.loads(sent.content)
    assert payload["system"] == "SYSTEM PROMPT" and payload["messages"] == [{"role": "user", "content": "THE CONTEXT"}]
    assert sent.headers["anthropic-version"]
    adapter, server, _ = make(AnthropicAdapter, ok(anthropic_body()), env={**ENV, "ANTHROPIC_API_VERSION": "2099-01-01"})
    adapter.complete(REQUEST)
    assert server.requests[0].headers["anthropic-version"] == "2099-01-01"


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_missing_key_and_missing_model_fail_before_any_request(adapter_cls, body, url):
    adapter, server, _ = make(adapter_cls, ok(body()), env={})
    with pytest.raises(ProviderError, match="is not set"):
        adapter.complete(REQUEST)
    adapter, server, _ = make(adapter_cls, ok(body()))
    with pytest.raises(ProviderError, match="no model configured"):
        adapter.complete(ProviderRequest(role=AnalysisRole.COLLECTION, prompt="p", context="c", model=None))
    assert server.requests == []


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_incomplete_answers_are_errors_not_drafts(adapter_cls, body, url):
    bad = ("length", "content_filter") if adapter_cls is not AnthropicAdapter else ("max_tokens", "refusal")
    for reason in bad:
        kwargs = {"finish": reason} if adapter_cls is not AnthropicAdapter else {"stop": reason}
        adapter, _, _ = make(adapter_cls, ok(body(**kwargs)))
        with pytest.raises(ProviderError, match="not complete"):
            adapter.complete(REQUEST)
    adapter, _, _ = make(adapter_cls, ok(body(text="   ")))
    with pytest.raises(ProviderError, match="no text"):
        adapter.complete(REQUEST)


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_malformed_responses_are_errors(adapter_cls, body, url):
    for response in (httpx.Response(200, text="<html>"), ok([1, 2]), ok({}), ok({"choices": [], "content": None})):
        adapter, _, _ = make(adapter_cls, response)
        with pytest.raises(ProviderError):
            adapter.complete(REQUEST)


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_client_errors_are_not_retried_and_never_leak_body_or_key(adapter_cls, body, url):
    leak = httpx.Response(401, json={"error": {"type": "authentication_error",
                                               "message": f"Bad key {KEY} for prompt THE CONTEXT"}})
    adapter, server, sleeps = make(adapter_cls, leak)
    with pytest.raises(ProviderError) as info:
        adapter.complete(REQUEST)
    message = str(info.value)
    assert "HTTP 401" in message and "authentication_error" in message
    assert KEY not in message and "THE CONTEXT" not in message and "Bad key" not in message
    assert len(server.requests) == 1 and sleeps == []


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_rate_limit_is_retried_honouring_retry_after_then_succeeds(adapter_cls, body, url):
    limited = httpx.Response(429, headers={"retry-after": "7"}, json={})
    adapter, server, sleeps = make(adapter_cls, limited, ok(body()))
    response = adapter.complete(REQUEST)
    assert response.attempts == 2 and sleeps == [7.0] and len(server.requests) == 2


def test_retry_after_is_capped():
    adapter, _, sleeps = make(OpenAIAdapter, httpx.Response(429, headers={"retry-after": "9999"}), ok(chat_body()))
    adapter.complete(REQUEST)
    assert sleeps == [30.0]


@pytest.mark.parametrize("adapter_cls, body, url", ADAPTERS)
def test_persistent_server_errors_and_timeouts_give_up_after_three_attempts(adapter_cls, body, url):
    adapter, server, sleeps = make(adapter_cls, httpx.Response(503, json={}))
    with pytest.raises(ProviderError, match="gave up after 3 attempts: HTTP 503"):
        adapter.complete(REQUEST)
    assert len(server.requests) == 3 and len(sleeps) == 2

    adapter, server, _ = make(adapter_cls, httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError, match="gave up after 3 attempts: network error"):
        adapter.complete(REQUEST)
    assert len(server.requests) == 3


def test_redirects_are_never_followed():
    adapter, server, _ = make(OpenAIAdapter, httpx.Response(302, headers={"location": "https://evil.example/"}))
    with pytest.raises(ProviderError, match="HTTP 302"):
        adapter.complete(REQUEST)
    assert len(server.requests) == 1


def test_chunked_mistral_content_is_joined():
    body = chat_body()
    body["choices"][0]["message"]["content"] = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    adapter, _, _ = make(MistralAdapter, ok(body))
    assert adapter.complete(REQUEST).text == "AB"


# --- whole pipeline through three different vendors ---------------------------

def test_pipeline_runs_across_three_vendors_chosen_only_by_configuration():
    seen = {}

    def route(request):
        host = request.url.host
        payload = json.loads(request.content)
        seen.setdefault(host, []).append(payload)
        if host == "api.anthropic.com":
            return ok(anthropic_body(text="counter findings [S1]", model="claude-served"))
        text = {"api.mistral.ai": "collected notes [S1]", "api.openai.com": "openai text [S1]"}[host]
        return ok(chat_body(text=text, model=f"{host}-served"))

    registry = build_registry(ENV, transport=httpx.MockTransport(route), sleep=lambda s: None)
    config = AnalysisConfig.from_env({
        **{f"ANALYSIS_{r}_MODEL": "configured" for r in ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")},
        "ANALYSIS_COLLECTION_PROVIDER": "mistral",
        "ANALYSIS_MAIN_ANALYSIS_PROVIDER": "openai",
        "ANALYSIS_COUNTER_ANALYSIS_PROVIDER": "anthropic",
        "ANALYSIS_FINAL_REPORT_PROVIDER": "openai",
        "ANALYSIS_MAIN_ANALYSIS_MAX_OUTPUT_TOKENS": "5000",
        "ANALYSIS_MAIN_ANALYSIS_PRICE_INPUT_PER_MTOK": "1",
        "ANALYSIS_MAIN_ANALYSIS_PRICE_OUTPUT_PER_MTOK": "2",
    })
    draft = AnalysisPipeline(config, registry, PromptLibrary()).analyze("[S1] source text")

    assert [s.response.provider for s in draft.stages] == ["mistral", "openai", "anthropic", "openai"]
    assert draft.warnings == () and draft.status.value == "draft"
    assert all(s.prompt_version == "v1" and len(s.prompt_sha256) == 64 for s in draft.stages)
    assert draft.main_analysis.estimated_cost == pytest.approx((10 * 1 + 5 * 2) / 1_000_000)
    assert draft.collection.estimated_cost is None  # no prices configured: unknown, not zero
    # The reviewer on another vendor really receives the main analysis to challenge:
    review_request = json.dumps(seen["api.anthropic.com"][0], ensure_ascii=False)
    assert "openai text [S1]" in review_request and "Main analysis to review" in review_request
    # The final report receives both, so disagreement can be reported:
    final_request = json.dumps(seen["api.openai.com"][-1], ensure_ascii=False)
    assert "counter findings [S1]" in final_request and "openai text [S1]" in final_request
    assert seen["api.openai.com"][0]["max_completion_tokens"] == 5000


def test_same_vendor_and_model_for_review_is_flagged_as_not_independent():
    def route(request):
        return ok(chat_body(text="text", model="same-model"))

    registry = build_registry(ENV, transport=httpx.MockTransport(route), sleep=lambda s: None)
    config = AnalysisConfig.from_env({f"ANALYSIS_{r}_PROVIDER": "openai"
                                      for r in ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")}
                                     | {f"ANALYSIS_{r}_MODEL": "m" for r in ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")})
    draft = AnalysisPipeline(config, registry).analyze("source")
    assert len(draft.warnings) == 1 and "not an independent check" in draft.warnings[0]


def test_unavailable_provider_stops_the_run_without_a_draft():
    def route(request):
        if request.url.host == "api.anthropic.com":
            return httpx.Response(529, json={"error": {"type": "overloaded_error"}})
        return ok(chat_body(text="text"))

    registry = build_registry(ENV, transport=httpx.MockTransport(route), sleep=lambda s: None)
    config = AnalysisConfig.from_env({
        "ANALYSIS_COLLECTION_PROVIDER": "openai", "ANALYSIS_MAIN_ANALYSIS_PROVIDER": "openai",
        "ANALYSIS_COUNTER_ANALYSIS_PROVIDER": "anthropic", "ANALYSIS_FINAL_REPORT_PROVIDER": "openai",
        **{f"ANALYSIS_{r}_MODEL": "m" for r in ("COLLECTION", "MAIN_ANALYSIS", "COUNTER_ANALYSIS", "FINAL_REPORT")},
    })
    with pytest.raises(ProviderError, match="anthropic: gave up after 3 attempts: HTTP 529 \\(overloaded_error\\)"):
        AnalysisPipeline(config, registry).analyze("source")
