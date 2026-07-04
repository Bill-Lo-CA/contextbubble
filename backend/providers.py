import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AgentProviderError(RuntimeError):
    def __init__(self, error_code, message=""):
        self.error_code = error_code
        super().__init__(message or error_code)


def extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = min([index for index in (text.find("["), text.find("{")) if index >= 0], default=-1)
    if start > 0:
        text = text[start:]
    if text.startswith("[") and text.rfind("]") >= 0:
        text = text[:text.rfind("]") + 1]
    if text.startswith("{") and text.rfind("}") >= 0:
        text = text[:text.rfind("}") + 1]
    return json.loads(text)


def gemini_generate(prompt, api_key, model):
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for agent analysis")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    data = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode()
    request = Request(url, data=data, headers={"content-type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Gemini request failed: {error}") from error
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts)
    return extract_json(text)


def ollama_generate(prompt, base_url, model, schema=None):
    del schema
    data = json.dumps({
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    request = Request(f"{base_url}/api/generate", data=data, headers={"content-type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode())
    except TimeoutError as error:
        raise AgentProviderError("OLLAMA_TIMEOUT") from error
    except (HTTPError, URLError) as error:
        raise AgentProviderError("OLLAMA_UNAVAILABLE") from error
    except json.JSONDecodeError as error:
        raise AgentProviderError("OLLAMA_INVALID_RESPONSE") from error
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise AgentProviderError("OLLAMA_INVALID_RESPONSE")
    try:
        return extract_json(text)
    except json.JSONDecodeError as error:
        raise AgentProviderError("OLLAMA_INVALID_JSON") from error
