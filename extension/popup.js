const analyze = document.getElementById("analyze");
const learnerLevel = document.getElementById("learner-level");
const status = document.getElementById("status");
const STATUS_KEY = "contextbubbleStatus";

chrome.storage.session.get(STATUS_KEY, (saved) => {
  if (saved[STATUS_KEY]) status.textContent = saved[STATUS_KEY];
});

function setStatus(text) {
  status.textContent = text;
  chrome.storage.session.set({ [STATUS_KEY]: text });
}

function getVideoId(tab) {
  return new URL(tab.url).searchParams.get("v") || "";
}

function sendAnalyzeMessage(tabId) {
  const message = {
    type: "contextbubble:analyze-v2",
    learnerLevel: learnerLevel.value,
  };

  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      const error = chrome.runtime.lastError?.message;
      resolve({ error, response });
    });
  });
}

async function analyzeTab(tabId) {
  await chrome.scripting.insertCSS({ target: { tabId }, files: ["styles.css"] });
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  return sendAnalyzeMessage(tabId);
}

analyze.addEventListener("click", async () => {
  analyze.disabled = true;
  setStatus("Analyzing...");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const videoId = tab ? getVideoId(tab) : "";
    if (!tab?.id || !videoId) throw new Error("Open a YouTube watch page first.");

    const { error, response } = await analyzeTab(tab.id);
    if (response?.status === "already-running") {
      setStatus("Analysis is already running.");
      return;
    }
    const segmentStatus = response?.segmentCount
      ? `${response.segmentCount} subtitle segments ready. `
      : "";
    const chunkCountStatus = response?.chunksAnalyzed
      ? `${response.chunksAnalyzed} chunks analyzed. `
      : "";
    const chunkStatus = response?.chunkStart !== undefined
      ? `Chunk ${Math.round(response.chunkStart)}-${Math.round(response.chunkEnd)}s. `
      : "";
    const syncStatus = response?.requestedAt !== undefined && response?.receivedAt !== undefined
      ? `Requested ${Math.round(response.requestedAt)}s, received ${Math.round(response.receivedAt)}s, replied ${Math.round(response.respondedAt)}s. `
      : "";
    setStatus(error || response?.error
      ? error || response.error
      : `${segmentStatus}${chunkCountStatus}${chunkStatus}${syncStatus}Ready: ${response.count} bubbles for ${response.videoId}.`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    analyze.disabled = false;
  }
});
