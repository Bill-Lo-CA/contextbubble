const BY_VIDEO_KEY = "contextbubbleByVideo";
const ACTIVE_VIDEO_KEY = "contextbubbleActiveVideoId";
const OWNER_KEY = "contextbubbleAnalysisOwner";
const captions = document.getElementById("captions");
let activeVideoId = "";
let renderedSentenceCount = 0;
let renderedSentenceVideoId = "";

function formatTime(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
  const secs = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
}

function normalizeText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function lastCaptionVisible() {
  const last = captions.querySelector(".caption:last-child");
  if (!last) return false;
  const rect = last.getBoundingClientRect();
  return rect.top < window.innerHeight && rect.bottom > 0;
}

function renderSentences(entries = [], { autoScroll = false } = {}) {
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
    text.textContent = normalizeText(entry.source_text || entry.text);
    item.append(time, text);
    if (entry.translated_text) {
      const translation = document.createElement("div");
      translation.className = "translation";
      translation.textContent = normalizeText(entry.translated_text);
      item.append(translation);
    } else if (entry.translation_status === "pending") {
      const pending = document.createElement("div");
      pending.className = "debug";
      pending.textContent = "Translating...";
      item.append(pending);
    } else if (entry.translation_status === "failed") {
      const failed = document.createElement("div");
      failed.className = "debug";
      failed.textContent = "Translation failed.";
      item.append(failed);
    } else if (entry.translation_status === "skipped") {
      const skipped = document.createElement("div");
      skipped.className = "debug";
      skipped.textContent = "Translation skipped.";
      item.append(skipped);
    }
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
  if (autoScroll) scrollTo(0, document.body.scrollHeight);
}

function renderCaptions(log = []) {
  const autoScroll = window.scrollY + window.innerHeight >= document.body.scrollHeight - 40;
  renderSentences(log.map((entry, index) => ({
    id: `caption-${index}`,
    start_seconds: 0,
    end_seconds: 0,
    text: normalizeText(entry.source_text || entry.text),
    source_text: normalizeText(entry.source_text || entry.text),
    translated_text: normalizeText(entry.translated_text),
    translation_status: entry.translation_status || "",
    timeText: entry.timeText || "",
    source_segment_ids: [],
  })), { autoScroll });
}

function renderSaved(saved) {
  const byVideo = saved[BY_VIDEO_KEY] || {};
  const stateInfo = stateToRender(byVideo, saved[OWNER_KEY]);
  const state = stateInfo.state;
  const sentences = state.allSentenceEntries?.length
    ? state.allSentenceEntries
    : state.shownSentenceEntries || [];
  if (sentences.length) {
    const selectedVideoId = stateInfo.key || activeVideoId;
    const sameVideo = selectedVideoId === renderedSentenceVideoId;
    const shouldScroll = sameVideo && sentences.length > renderedSentenceCount && lastCaptionVisible();
    renderSentences(sentences, { autoScroll: shouldScroll });
    renderedSentenceCount = sentences.length;
    renderedSentenceVideoId = selectedVideoId;
    return;
  }
  renderedSentenceCount = 0;
  renderedSentenceVideoId = "";
  if (state.captionLog?.length) {
    renderCaptions(state.captionLog);
    return;
  }
  renderCaptions([]);
}

function stateToRender(byVideo, owner) {
  const ownerVideoId = owner?.videoId || "";
  if (!ownerVideoId) return { key: "", state: {} };
  return { key: ownerVideoId, state: byVideo[ownerVideoId] || {} };
}

async function refreshActiveVideo() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const saved = await chrome.storage.local.get([BY_VIDEO_KEY, ACTIVE_VIDEO_KEY, OWNER_KEY]);
  activeVideoId = saved[OWNER_KEY]?.videoId || "";
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
    chrome.storage.local.get([BY_VIDEO_KEY, OWNER_KEY], renderSaved);
  }
  if (changes[ACTIVE_VIDEO_KEY] || changes[OWNER_KEY]) {
    refreshActiveVideo();
  }
});
