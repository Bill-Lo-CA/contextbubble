import time

from config import GEMINI_API_KEY, GEMINI_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL
from providers import AgentProviderError, gemini_generate, ollama_generate


MAX_PROVIDER_ATTEMPTS = 3
PROVIDER_RETRY_BACKOFF_SECONDS = (2, 4)  # wait before attempt 2, then before attempt 3
RETRYABLE_ERROR_CODES = {
    "GEMINI_RATE_LIMITED", "GEMINI_TIMEOUT", "GEMINI_UNAVAILABLE",
    "GEMINI_SERVER_ERROR", "GEMINI_INVALID_JSON", "GEMINI_INVALID_RESPONSE",
    "OLLAMA_TIMEOUT", "OLLAMA_UNAVAILABLE", "OLLAMA_INVALID_RESPONSE", "OLLAMA_INVALID_JSON",
}


class Provider:
    def generate_json(self, prompt, schema=None):
        raise NotImplementedError


class GeminiProvider(Provider):
    def __init__(self, model=None, api_key=None):
        self.model = model or GEMINI_MODEL
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY

    def generate_json(self, prompt, schema=None):
        return gemini_generate(prompt, self.api_key, self.model, schema=schema)


class OllamaProvider(Provider):
    def __init__(self, model=None, base_url=None):
        self.model = model or OLLAMA_MODEL
        self.base_url = base_url or OLLAMA_BASE_URL

    def generate_json(self, prompt, schema=None):
        return ollama_generate(prompt, self.base_url, self.model, schema=schema)


def resolve_provider(mode, *, gemini_model=None, ollama_model=None, disabled_error_code="PROVIDER_DISABLED", disabled_message=""):
    if mode == "gemini":
        return GeminiProvider(model=gemini_model)
    if mode == "ollama":
        return OllamaProvider(model=ollama_model)
    raise AgentProviderError(disabled_error_code, disabled_message or f"no LLM provider selected for mode={mode!r}")


def generate_json_with_retry(provider, prompt, schema=None):
    # Opt-in: only callers that want resilience against transient provider
    # failures should call this instead of provider.generate_json(...) directly.
    # translation_agents.py/semantic_splitter.py call generate_json directly and
    # are unaffected, since they're latency-sensitive and shouldn't inherit a
    # worst-case multi-attempt wait.
    last_error = None
    for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
        try:
            return provider.generate_json(prompt, schema=schema)
        except AgentProviderError as error:
            last_error = error
            if error.error_code not in RETRYABLE_ERROR_CODES or attempt == MAX_PROVIDER_ATTEMPTS:
                raise
            time.sleep(PROVIDER_RETRY_BACKOFF_SECONDS[attempt - 1])
    raise last_error
