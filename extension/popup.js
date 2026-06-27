const analyze = document.getElementById("analyze");
const status = document.getElementById("status");

analyze.addEventListener("click", async () => {
  analyze.disabled = true;
  status.textContent = "Analyzing...";

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    status.textContent = "No active tab.";
    analyze.disabled = false;
    return;
  }

  chrome.tabs.sendMessage(tab.id, { type: "contextbubble:analyze" }, (response) => {
    analyze.disabled = false;
    const error = chrome.runtime.lastError?.message || response?.error;
    status.textContent = error
      ? error
      : `Ready: ${response.count} bubbles for ${response.videoId}.`;
  });
});
