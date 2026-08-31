import hashlib
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config
from auth import redact_secret_text


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
    "last_invalid_response_length": None,
    "last_invalid_response_sha256": None,
    "last_json_error": None,
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
        if error.code >= 500:
            return "GEMINI_SERVER_ERROR", "failed", error.code, f"HTTP {error.code}"
        return "GEMINI_HTTP_ERROR", "failed", error.code, f"HTTP {error.code}"
    if isinstance(error, TimeoutError):
        return "GEMINI_TIMEOUT", "failed", None, "Gemini request timed out"
    return "GEMINI_UNAVAILABLE", "failed", None, str(getattr(error, "reason", error))


def _debug_log_llm_response(provider_name, error_code, text):
    if not config.DEBUG_LLM_RESPONSES:
        return
    try:
        with open(config.LLM_DEBUG_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps({
                "time": time.time(),
                "provider": provider_name,
                "error_code": error_code,
                "text": redact_secret_text(text)[:20000],
            }) + "\n")
    except OSError:
        pass


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


def gemini_generate(prompt, api_key, model, schema=None):
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
    generation_config = {"temperature": 0.2, "responseMimeType": "application/json"}
    if schema is not None:
        generation_config["responseSchema"] = schema
    data = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
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
            response_text = response.read().decode()
            payload = json.loads(response_text)
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
    except json.JSONDecodeError as error:
        update_gemini_status(
            status="failed",
            last_error_at=time.time(),
            last_error_code="GEMINI_INVALID_RESPONSE",
            last_http_status=None,
            last_message="Gemini returned a non-JSON response envelope",
            total_failures=GEMINI_STATUS["total_failures"] + 1,
        )
        _debug_log_llm_response("gemini", "GEMINI_INVALID_RESPONSE", response_text)
        raise AgentProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned a non-JSON response envelope") from error
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
            last_invalid_response_length=len(text),
            last_invalid_response_sha256=hashlib.sha256(text.encode()).hexdigest(),
            last_json_error={"message": error.msg, "line": error.lineno, "column": error.colno, "position": error.pos},
            total_failures=GEMINI_STATUS["total_failures"] + 1,
        )
        _debug_log_llm_response("gemini", "GEMINI_INVALID_JSON", text)
        raise AgentProviderError("GEMINI_INVALID_JSON", "Gemini returned invalid JSON") from error
    update_gemini_status(
        status="ok",
        last_success_at=time.time(),
        last_error_code=None,
        last_http_status=None,
        last_message="",
        last_invalid_response_length=None,
        last_invalid_response_sha256=None,
        last_json_error=None,
    )
    return result



def ollama_generate(prompt, base_url, model, schema=None):
    data = json.dumps({
        "model": model,
        "prompt": prompt,
        "format": schema if schema is not None else "json",
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    request = Request(f"{base_url}/api/generate", data=data, headers={"content-type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=90) as response:
            response_text = response.read().decode()
            payload = json.loads(response_text)
    except TimeoutError as error:
        raise AgentProviderError("OLLAMA_TIMEOUT") from error
    except (HTTPError, URLError) as error:
        raise AgentProviderError("OLLAMA_UNAVAILABLE") from error
    except json.JSONDecodeError as error:
        _debug_log_llm_response("ollama", "OLLAMA_INVALID_RESPONSE", response_text)
        raise AgentProviderError("OLLAMA_INVALID_RESPONSE") from error
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise AgentProviderError("OLLAMA_INVALID_RESPONSE")
    try:
        return extract_json(text)
    except json.JSONDecodeError as error:
        _debug_log_llm_response("ollama", "OLLAMA_INVALID_JSON", text)
        raise AgentProviderError("OLLAMA_INVALID_JSON") from error
