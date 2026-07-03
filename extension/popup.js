const analyze = document.getElementById("analyze");
const reanalyze = document.getElementById("reanalyze");
const openCaptions = document.getElementById("open-captions");
const pair = document.getElementById("pair");
const checkBackend = document.getElementById("check-backend");
const pairingCode = document.getElementById("pairing-code");
const demoMode = document.getElementById("demo-mode");
const learnerLevel = document.getElementById("learner-level");
const status = document.getElementById("status");
const API_BASE = "http://127.0.0.1:8000";
const STATUS_KEY = "contextbubbleStatus";
const SESSION_TOKEN_KEY = "contextbubbleSessionToken";

chrome.storage.session.get(STATUS_KEY, (saved) => {
  if (saved[STATUS_KEY]) status.textContent = saved[STATUS_KEY];
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes[STATUS_KEY]) {
    status.textContent = changes[STATUS_KEY].newValue;
  }
});

function setStatus(text) {
  status.textContent = text;
  chrome.storage.session.set({ [STATUS_KEY]: text });
}

function getVideoId(tab) {
  return new URL(tab.url).searchParams.get("v") || "";
}

async function sessionToken() {
  const saved = await chrome.storage.session.get(SESSION_TOKEN_KEY);
  return saved[SESSION_TOKEN_KEY] || "";
}

async function fetchBackend(path, options = {}) {
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
    if (response.status === 401 && path !== "/api/pair") throw new Error("Invalid or expired session.");
    throw new Error(result.error || "Backend request failed.");
  }
  return result;
}

async function pairBackend() {
  const code = pairingCode.value.trim();
  if (!code) throw new Error("Enter the backend pairing code.");
  const result = await fetchBackend("/api/pair", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ pairing_code: code }),
  });
  await chrome.storage.session.set({ [SESSION_TOKEN_KEY]: result.session_token });
  pairingCode.value = "";
  return result;
}

async function checkBackendConnection() {
  const token = await sessionToken();
  if (!token) throw new Error("Pair the backend first.");
  await fetchBackend("/api/health", {
    headers: { "authorization": `Bearer ${token}` },
  });
}

async function sendAnalyzeMessage(tabId, forceRefresh = false) {
  const token = await sessionToken();
  const message = {
    type: "contextbubble:analyze-v2",
    sessionToken: token,
    demoMode: demoMode.checked,
    learnerLevel: learnerLevel.value,
    forceRefresh,
  };

  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      const error = chrome.runtime.lastError?.message;
      resolve({ error, response });
    });
  });
}

async function analyzeTab(tabId, forceRefresh = false) {
  return sendAnalyzeMessage(tabId, forceRefresh);
}

async function getActiveYoutubeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const videoId = tab ? getVideoId(tab) : "";
  if (!tab?.id || !videoId) throw new Error("Open a YouTube watch page first.");
  return tab;
}

openCaptions.addEventListener("click", async () => {
  try {
    const tab = await getActiveYoutubeTab();
    await chrome.sidePanel.open({ tabId: tab.id });
  } catch (error) {
    setStatus(error.message);
  }
});

pair.addEventListener("click", async () => {
  pair.disabled = true;
  setStatus("Pairing backend...");
  try {
    await pairBackend();
    setStatus("Backend paired for this browser session.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    pair.disabled = false;
  }
});

checkBackend.addEventListener("click", async () => {
  checkBackend.disabled = true;
  setStatus("Checking backend...");
  try {
    await checkBackendConnection();
    setStatus("Backend connected.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    checkBackend.disabled = false;
  }
});

async function runAnalyze(forceRefresh = false) {
  analyze.disabled = true;
  reanalyze.disabled = true;
  setStatus(forceRefresh ? "Starting fresh analysis..." : "Preparing video...");

  try {
    const tab = await getActiveYoutubeTab();
    if (!await sessionToken()) throw new Error("Pair the backend first.");

    const { error, response } = await analyzeTab(tab.id, forceRefresh);
    if (response?.status === "already-running") {
      setStatus("Analysis is already running.");
      return;
    }
    if (response?.status === "stale-result-discarded") {
      setStatus("Analysis finished, but the page changed. Result discarded.");
      return;
    }
    setStatus(error || response?.error
      ? error || response.error
      : `Ready: ${response.count} bubbles for ${response.videoId} from ${response.transcriptSource}.`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    analyze.disabled = false;
    reanalyze.disabled = false;
  }
}

analyze.addEventListener("click", () => runAnalyze(false));
reanalyze.addEventListener("click", () => runAnalyze(true));
