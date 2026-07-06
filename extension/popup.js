const analyze = document.getElementById("analyze");
const reanalyze = document.getElementById("reanalyze");
const openCaptions = document.getElementById("open-captions");
const pair = document.getElementById("pair");
const resendCode = document.getElementById("resend-code");
const checkBackend = document.getElementById("check-backend");
const pairingDigits = Array.from(document.querySelectorAll(".pairing-digit"));
const demoMode = document.getElementById("demo-mode");
const learnerLevel = document.getElementById("learner-level");
const status = document.getElementById("status");
const SESSION_TOKEN_KEY = "contextbubbleSessionToken";
const BY_VIDEO_KEY = "contextbubbleByVideo";
let activeVideoId = "";

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[BY_VIDEO_KEY] && activeVideoId) {
    const text = changes[BY_VIDEO_KEY].newValue?.[activeVideoId]?.status;
    if (text) status.textContent = text;
  }
});

function setStatus(text) {
  status.textContent = text;
}

function getVideoId(tab) {
  return new URL(tab.url).searchParams.get("v") || "";
}

async function sessionToken() {
  const saved = await chrome.storage.session.get(SESSION_TOKEN_KEY);
  return saved[SESSION_TOKEN_KEY] || "";
}

async function pairBackend() {
  const code = pairingDigits.map((input) => input.value).join("");
  if (!code) throw new Error("Enter the backend pairing code.");
  if (!/^\d{6}$/.test(code)) throw new Error("Enter the six digit pairing code.");
  const result = await contextbubbleBackend.fetchJson("/api/pair", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ pairing_code: code }),
  });
  await chrome.storage.session.set({ [SESSION_TOKEN_KEY]: result.session_token });
  clearPairingCode();
  return result;
}

async function resendPairingCode() {
  await contextbubbleBackend.fetchJson("/api/pair/resend", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  });
}

function clearPairingCode() {
  for (const input of pairingDigits) input.value = "";
}

function fillPairingCode(text, startIndex = 0) {
  const digits = String(text || "").replace(/\D/g, "").slice(0, 6 - startIndex);
  digits.split("").forEach((digit, offset) => {
    pairingDigits[startIndex + offset].value = digit;
  });
  const next = Math.min(startIndex + digits.length, pairingDigits.length - 1);
  pairingDigits[next]?.focus();
}

async function checkBackendConnection() {
  const token = await sessionToken();
  if (!token) throw new Error("Pair the backend first.");
  await contextbubbleBackend.fetchJson("/api/health", {
    headers: { "authorization": `Bearer ${token}` },
  });
}

function contentScriptUnavailable(error) {
  return error && (
    error.includes("Receiving end does not exist")
    || error.includes("Could not establish connection")
  );
}

function sendMessage(tabId, message) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      const error = chrome.runtime.lastError?.message;
      resolve({ error, response });
    });
  });
}

async function injectContentScript(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["backendClient.js", "contentOverlay.js", "content.js"],
  });
}

async function sendAnalyzeMessage(tabId, videoId, forceRefresh = false) {
  const token = await sessionToken();
  const message = {
    type: "contextbubble:analyze-v2",
    videoId,
    sessionToken: token,
    demoMode: demoMode.checked,
    learnerLevel: learnerLevel.value,
    forceRefresh,
  };

  let result = await sendMessage(tabId, message);
  if (!contentScriptUnavailable(result.error)) return result;

  try {
    await injectContentScript(tabId);
    result = await sendMessage(tabId, message);
  } catch {
    return { error: "Content script is not ready on this YouTube page. Refresh the video page and try again." };
  }
  if (contentScriptUnavailable(result.error)) {
    return { error: "Content script is not ready on this YouTube page. Refresh the video page and try again." };
  }
  return result;
}

async function analyzeTab(tabId, videoId, forceRefresh = false) {
  return sendAnalyzeMessage(tabId, videoId, forceRefresh);
}

async function getActiveYoutubeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const videoId = tab ? getVideoId(tab) : "";
  if (!tab?.id || !videoId) throw new Error("Open a YouTube watch page first.");
  activeVideoId = videoId;
  return { tab, videoId };
}

async function loadActiveStatus() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeVideoId = tab ? getVideoId(tab) : "";
  if (!activeVideoId) return;
  const saved = await chrome.storage.local.get(BY_VIDEO_KEY);
  const text = saved[BY_VIDEO_KEY]?.[activeVideoId]?.status;
  if (text) status.textContent = text;
}

loadActiveStatus();

pairingDigits.forEach((input, index) => {
  input.addEventListener("input", () => {
    fillPairingCode(input.value, index);
    if (input.value && index < pairingDigits.length - 1) pairingDigits[index + 1].focus();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Backspace" && !input.value && index > 0) pairingDigits[index - 1].focus();
  });
  input.addEventListener("paste", (event) => {
    event.preventDefault();
    fillPairingCode(event.clipboardData.getData("text"), index);
  });
});

openCaptions.addEventListener("click", async () => {
  try {
    const { tab } = await getActiveYoutubeTab();
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

resendCode.addEventListener("click", async () => {
  resendCode.disabled = true;
  setStatus("Requesting a new pairing code...");
  try {
    await resendPairingCode();
    clearPairingCode();
    pairingDigits[0]?.focus();
    setStatus("New pairing code printed in the backend terminal.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    resendCode.disabled = false;
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
    const { tab, videoId } = await getActiveYoutubeTab();
    if (!await sessionToken()) throw new Error("Pair the backend first.");

    const { error, response } = await analyzeTab(tab.id, videoId, forceRefresh);
    if (response?.status === "already-running") {
      setStatus("Analysis is already running.");
      return;
    }
    if (response?.status === "stale-result-discarded") {
      setStatus("Analysis finished, but the page changed. Result discarded.");
      return;
    }
    if (response?.status === "analysis-finished-background") {
      setStatus(`Ready in background: ${response.count} bubbles for ${response.videoId} from ${response.transcriptSource}.`);
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
