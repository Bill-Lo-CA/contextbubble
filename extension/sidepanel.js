const BY_VIDEO_KEY = "contextbubbleByVideo";
const ACTIVE_VIDEO_KEY = "contextbubbleActiveVideoId";
const captions = document.getElementById("captions");
let activeVideoId = "";

function getVideoId(url) {
  try {
    return new URL(url).searchParams.get("v") || "";
  } catch {
    return "";
  }
}

function formatTime(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
  const secs = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
}

function normalizeText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function renderSentences(entries = []) {
  captions.textContent = "";
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No transcript yet.";
    captions.append(empty);
    return;
  }

  for (const entry of entries) {
    const item = document.createElement("article");
    item.className = "caption";
    const time = document.createElement("div");
    time.className = "time";
    time.textContent = entry.timeText || `${formatTime(entry.start_seconds)}-${formatTime(entry.end_seconds)}`;
    const text = document.createElement("div");
    text.textContent = normalizeText(entry.text);
    item.append(time, text);
    if (entry.source_segment_ids?.length) {
      const debug = document.createElement("details");
      debug.className = "debug";
      const summary = document.createElement("summary");
      summary.textContent = "Source";
      const ids = document.createElement("div");
      ids.textContent = entry.source_segment_ids.join(", ");
      debug.append(summary, ids);
      item.append(debug);
    }
    captions.append(item);
  }
  scrollTo(0, document.body.scrollHeight);
}

function renderCaptions(log = []) {
  renderSentences(log.map((entry, index) => ({
    id: `caption-${index}`,
    start_seconds: 0,
    end_seconds: 0,
    text: normalizeText(entry.text),
    timeText: entry.timeText || "",
    source_segment_ids: [],
  })));
}

function renderSaved(saved) {
  const byVideo = saved[BY_VIDEO_KEY] || {};
  const state = byVideo[activeVideoId] || latestVideoState(byVideo);
  const sentences = state.sentenceEntries || [];
  if (sentences.length) {
    renderSentences(sentences);
    return;
  }
  renderCaptions(state.captionLog || []);
}

function latestVideoState(byVideo) {
  return Object.values(byVideo).sort((left, right) => {
    return (right.updatedAt || 0) - (left.updatedAt || 0);
  })[0] || {};
}

async function refreshActiveVideo() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const saved = await chrome.storage.local.get([BY_VIDEO_KEY, ACTIVE_VIDEO_KEY]);
  activeVideoId = saved[ACTIVE_VIDEO_KEY] || getVideoId(tab?.url || "");
  renderSaved(saved);
}

refreshActiveVideo();

chrome.tabs.onActivated?.addListener(refreshActiveVideo);
chrome.tabs.onUpdated?.addListener((_tabId, changeInfo) => {
  if (changeInfo.url || changeInfo.status === "complete") refreshActiveVideo();
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes[BY_VIDEO_KEY]) {
    chrome.storage.local.get(BY_VIDEO_KEY, renderSaved);
  }
  if (changes[ACTIVE_VIDEO_KEY]) {
    activeVideoId = changes[ACTIVE_VIDEO_KEY].newValue || "";
    chrome.storage.local.get(BY_VIDEO_KEY, renderSaved);
  }
});
