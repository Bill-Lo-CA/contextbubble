const analyze = document.getElementById("analyze");
const learnerLevel = document.getElementById("learner-level");
const subtitle = document.getElementById("subtitle");
const status = document.getElementById("status");
const API_BASE = "http://127.0.0.1:8000";

function getVideoId(tab) {
  return new URL(tab.url).searchParams.get("v") || "";
}

async function uploadSubtitle(videoId) {
  const file = subtitle.files?.[0];
  if (!file) return "";

  status.textContent = "Uploading subtitles...";
  const response = await fetch(`${API_BASE}/api/subtitles`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      video_id: videoId,
      filename: file.name,
      content: await file.text(),
    }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Subtitle upload failed.");
  return result;
}

analyze.addEventListener("click", async () => {
  analyze.disabled = true;
  status.textContent = "Analyzing...";

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const videoId = tab ? getVideoId(tab) : "";
    if (!tab?.id || !videoId) throw new Error("Open a YouTube watch page first.");

    const transcript = await uploadSubtitle(videoId);
    chrome.tabs.sendMessage(tab.id, {
      type: "contextbubble:analyze",
      learnerLevel: learnerLevel.value,
      transcriptId: transcript.transcript_id,
    }, (response) => {
      analyze.disabled = false;
      const error = chrome.runtime.lastError?.message || response?.error;
      const uploadStatus = transcript.segment_count
        ? `${transcript.segment_count} subtitle segments ready. `
        : "";
      status.textContent = error
        ? error
        : `${uploadStatus}Ready: ${response.count} bubbles for ${response.videoId}.`;
    });
  } catch (error) {
    status.textContent = error.message;
    analyze.disabled = false;
  }
});
