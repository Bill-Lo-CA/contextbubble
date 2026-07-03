const CAPTION_LOG_KEY = "contextbubbleCaptionLog";
const SENTENCE_ENTRIES_KEY = "contextbubbleSentenceEntries";
const captions = document.getElementById("captions");

function formatTime(seconds) {
  seconds = Math.max(0, Math.round(seconds || 0));
  const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
  const secs = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
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
    text.textContent = entry.text || "";
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
    text: entry.text || "",
    timeText: entry.timeText || "",
    source_segment_ids: [],
  })));
}

function renderSaved(saved) {
  const sentences = saved[SENTENCE_ENTRIES_KEY] || [];
  if (sentences.length) {
    renderSentences(sentences);
    return;
  }
  renderCaptions(saved[CAPTION_LOG_KEY] || []);
}

chrome.storage.local.get([SENTENCE_ENTRIES_KEY, CAPTION_LOG_KEY], renderSaved);

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes[SENTENCE_ENTRIES_KEY]) {
    const entries = changes[SENTENCE_ENTRIES_KEY].newValue || [];
    if (entries.length) {
      renderSentences(entries);
      return;
    }
  }
  if (changes[CAPTION_LOG_KEY]) {
    chrome.storage.local.get([SENTENCE_ENTRIES_KEY, CAPTION_LOG_KEY], renderSaved);
  }
});
