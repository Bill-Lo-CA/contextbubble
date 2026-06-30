const analyze = document.getElementById("analyze");
const learnerLevel = document.getElementById("learner-level");
const status = document.getElementById("status");

function getVideoId(tab) {
  return new URL(tab.url).searchParams.get("v") || "";
}

analyze.addEventListener("click", async () => {
  analyze.disabled = true;
  status.textContent = "Analyzing...";

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const videoId = tab ? getVideoId(tab) : "";
    if (!tab?.id || !videoId) throw new Error("Open a YouTube watch page first.");

    chrome.tabs.sendMessage(tab.id, {
      type: "contextbubble:analyze",
      learnerLevel: learnerLevel.value,
    }, (response) => {
      analyze.disabled = false;
      const error = chrome.runtime.lastError?.message || response?.error;
      const segmentStatus = response?.segmentCount
        ? `${response.segmentCount} subtitle segments ready. `
        : "";
      const chunkStatus = response?.chunkStart !== undefined
        ? `Chunk ${Math.round(response.chunkStart)}-${Math.round(response.chunkEnd)}s. `
        : "";
      status.textContent = error
        ? error
        : `${segmentStatus}${chunkStatus}Ready: ${response.count} bubbles for ${response.videoId}.`;
    });
  } catch (error) {
    status.textContent = error.message;
    analyze.disabled = false;
  }
});
