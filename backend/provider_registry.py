from config import GEMINI_API_KEY, GEMINI_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL
from providers import AgentProviderError, gemini_generate, ollama_generate


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
