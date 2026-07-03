const analyze = document.getElementById("analyze");
const reanalyze = document.getElementById("reanalyze");
const openCaptions = document.getElementById("open-captions");
const apiToken = document.getElementById("api-token");
const demoMode = document.getElementById("demo-mode");
const learnerLevel = document.getElementById("learner-level");
const status = document.getElementById("status");
const STATUS_KEY = "contextbubbleStatus";
const TOKEN_KEY = "contextbubbleApiToken";

chrome.storage.session.get(STATUS_KEY, (saved) => {
  if (saved[STATUS_KEY]) status.textContent = saved[STATUS_KEY];
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes[STATUS_KEY]) {
    status.textContent = changes[STATUS_KEY].newValue;
  }
});

chrome.storage.local.get(TOKEN_KEY, (saved) => {
  if (saved[TOKEN_KEY]) apiToken.value = saved[TOKEN_KEY];
});

function setStatus(text) {
  status.textContent = text;
  chrome.storage.session.set({ [STATUS_KEY]: text });
}

function getVideoId(tab) {
  return new URL(tab.url).searchParams.get("v") || "";
}

function sendAnalyzeMessage(tabId, forceRefresh = false) {
  chrome.storage.local.set({ [TOKEN_KEY]: apiToken.value });
  const message = {
    type: "contextbubble:analyze-v2",
    apiToken: apiToken.value,
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

async function runAnalyze(forceRefresh = false) {
  analyze.disabled = true;
  reanalyze.disabled = true;
  setStatus(forceRefresh ? "Starting fresh analysis..." : "Preparing video...");

  try {
    const tab = await getActiveYoutubeTab();
    if (!apiToken.value) throw new Error("Paste the backend API token first.");

    const { error, response } = await analyzeTab(tab.id, forceRefresh);
    if (response?.status === "already-running") {
      setStatus("Analysis is already running.");
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
