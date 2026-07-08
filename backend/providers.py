import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AgentProviderError(RuntimeError):
    def __init__(self, error_code, message=""):
        self.error_code = error_code
        super().__init__(message or error_code)


GEMINI_STATUS = {
    "status": "idle",
    "last_request_at": None,
    "last_success_at": None,
    "last_error_at": None,
    "last_error_code": None,
    "last_http_status": None,
    "last_message": "",
    "total_requests": 0,
    "total_failures": 0,
}


def gemini_status(api_key="", model=""):
    status = dict(GEMINI_STATUS)
    status["configured"] = bool(api_key)
    status["model"] = model
    if not api_key:
        status["status"] = "not_configured"
    return status


def update_gemini_status(**updates):
    GEMINI_STATUS.update(updates)


def gemini_error(error):
    if isinstance(error, HTTPError):
        if error.code == 429:
            return "GEMINI_RATE_LIMITED", "rate_limited", error.code, "HTTP 429 Too Many Requests"
        if error.code in (401, 403):
            return "GEMINI_AUTH_FAILED", "auth_failed", error.code, f"HTTP {error.code}"
        return "GEMINI_HTTP_ERROR", "failed", error.code, f"HTTP {error.code}"
    if isinstance(error, TimeoutError):
        return "GEMINI_TIMEOUT", "failed", None, "Gemini request timed out"
    return "GEMINI_UNAVAILABLE", "failed", None, str(getattr(error, "reason", error))


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
        update_gemini_status(
            status="not_configured",
            last_error_at=time.time(),
            last_error_code="GEMINI_NOT_CONFIGURED",
            last_http_status=None,
            last_message="GEMINI_API_KEY is not configured",
            total_failures=GEMINI_STATUS["total_failures"] + 1,
        )
        raise AgentProviderError("GEMINI_NOT_CONFIGURED", "GEMINI_API_KEY is not configured")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    data = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode()
    request = Request(url, data=data, headers={"content-type": "application/json"}, method="POST")
    update_gemini_status(
        status="requesting",
        last_request_at=time.time(),
        last_message="",
        total_requests=GEMINI_STATUS["total_requests"] + 1,
    )
    try:
        with urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError) as error:
        error_code, status, http_status, message = gemini_error(error)
        update_gemini_status(
            status=status,
            last_error_at=time.time(),
            last_error_code=error_code,
            last_http_status=http_status,
            last_message=message,
            total_failures=GEMINI_STATUS["total_failures"] + 1,
        )
        raise AgentProviderError(error_code, message) from error
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts)
    try:
        result = extract_json(text)
    except json.JSONDecodeError as error:
        update_gemini_status(
            status="failed",
            last_error_at=time.time(),
            last_error_code="GEMINI_INVALID_JSON",
            last_http_status=None,
            last_message="Gemini returned invalid JSON",
            total_failures=GEMINI_STATUS["total_failures"] + 1,
        )
        raise AgentProviderError("GEMINI_INVALID_JSON", "Gemini returned invalid JSON") from error
    update_gemini_status(
        status="ok",
        last_success_at=time.time(),
        last_error_code=None,
        last_http_status=None,
        last_message="",
    )
    return result



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
