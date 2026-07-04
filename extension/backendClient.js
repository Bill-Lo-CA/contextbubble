(function () {
  const API_BASE = "http://127.0.0.1:8000";

  async function fetchJson(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, options);
    } catch {
      throw new Error("Backend not running or unreachable.");
    }

    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error("Backend returned an invalid response.");
    }

    if (!response.ok) {
      if (response.status === 401 && path !== "/api/pair") {
        throw new Error("Invalid or expired session.");
      }
      throw new Error(result.error || "Backend request failed.");
    }
    return result;
  }

  globalThis.contextbubbleBackend = { fetchJson };
})();
