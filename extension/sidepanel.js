const CAPTION_LOG_KEY = "contextbubbleCaptionLog";
const captions = document.getElementById("captions");

function render(log = []) {
  captions.textContent = "";
  if (!log.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No captions yet.";
    captions.append(empty);
    return;
  }

  for (const entry of log) {
    const item = document.createElement("article");
    item.className = "caption";
    const time = document.createElement("div");
    time.className = "time";
    time.textContent = entry.timeText || "";
    const text = document.createElement("div");
    text.textContent = entry.text || "";
    item.append(time, text);
    captions.append(item);
  }
  scrollTo(0, document.body.scrollHeight);
}

chrome.storage.local.get(CAPTION_LOG_KEY, (saved) => {
  render(saved[CAPTION_LOG_KEY]);
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "local" && changes[CAPTION_LOG_KEY]) {
    render(changes[CAPTION_LOG_KEY].newValue);
  }
});
