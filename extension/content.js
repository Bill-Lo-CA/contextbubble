(function () {
  const API_BASE = "http://127.0.0.1:8000";

  let video;
  let bubbles = [];
  let transcriptSegments = [];
  let shownKeys = new Set();
  let activeVideoId;
  let activeBubble;
  let hideTimer;
  let lastCaptionText = "";
  let expanded = false;

  function getVideoId() {
    return new URLSearchParams(location.search).get("v") || "";
  }

  function findVideo() {
    video = document.querySelector("video");
    return video;
  }

  function removeBubble() {
    document.getElementById("contextbubble-root")?.remove();
    activeBubble = null;
    expanded = false;
    clearTimeout(hideTimer);
  }

  function readCaptionText() {
    return Array.from(document.querySelectorAll(".ytp-caption-segment"))
      .map((segment) => segment.textContent.trim())
      .filter(Boolean)
      .join(" ");
  }

  function renderCaptionPanel(text) {
    let panel = document.getElementById("contextbubble-caption");
    if (!text) {
      panel?.remove();
      lastCaptionText = "";
      return;
    }

    if (!panel) {
      panel = document.createElement("div");
      panel.id = "contextbubble-caption";
      document.body.appendChild(panel);
    }

    if (text !== lastCaptionText) {
      lastCaptionText = text;
      panel.textContent = text;
    }
  }

  async function fetchYoutubeTranscript(videoId, currentVideo) {
    const response = await fetch(`${API_BASE}/api/youtube-subtitles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video_id: videoId,
        current_time: currentVideo.currentTime,
        playback_rate: currentVideo.playbackRate || 1,
        chunk_seconds: 60,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Subtitle retrieval failed.");
    return result;
  }

  async function startAnalysis({ learnerLevel }) {
    const videoId = getVideoId();
    const currentVideo = findVideo();
    if (!videoId) throw new Error("Open a YouTube watch page first.");
    if (!currentVideo) throw new Error("No YouTube video element found.");

    const transcript = await fetchYoutubeTranscript(videoId, currentVideo);
    transcriptSegments = transcript.segments || [];

    const startResponse = await fetch(`${API_BASE}/api/analyses`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video_id: videoId,
        transcript_id: transcript.transcript_id,
        learner_level: learnerLevel,
        force_refresh: false,
      }),
    });
    if (!startResponse.ok) throw new Error("Backend did not start analysis.");

    const started = await startResponse.json();
    const resultResponse = await fetch(`${API_BASE}/api/analyses/${started.analysis_id}`);
    if (!resultResponse.ok) throw new Error("Backend did not return analysis.");

    const result = await resultResponse.json();
    bubbles = result.bubbles || [];
    shownKeys = new Set();
    removeBubble();
    return {
      videoId,
      count: bubbles.length,
      segmentCount: transcriptSegments.length,
      chunkStart: transcript.chunk_start_seconds,
      chunkEnd: transcript.chunk_end_seconds,
    };
  }

  function showBubble(bubble) {
    if (document.getElementById("contextbubble-root")) return;

    activeBubble = bubble;
    const root = document.createElement("aside");
    root.id = "contextbubble-root";
    root.innerHTML = `
      <div class="contextbubble-title"></div>
      <div class="contextbubble-text"></div>
      <div class="contextbubble-actions">
        <button type="button" data-action="expand">Expand</button>
        <button type="button" data-action="known">I know this</button>
        <button type="button" data-action="dismiss">Dismiss</button>
      </div>
    `;

    root.querySelector(".contextbubble-title").textContent = bubble.concept;
    renderText(root);
    root.addEventListener("click", handleBubbleClick);
    document.body.appendChild(root);
    hideTimer = setTimeout(() => {
      if (!expanded && activeBubble?.id === bubble.id) removeBubble();
    }, 8000);
  }

  function renderText(root) {
    if (!activeBubble) return;
    const text = expanded
      ? `${activeBubble.short_explanation} ${activeBubble.expanded_explanation || ""}`
      : activeBubble.short_explanation;
    root.querySelector(".contextbubble-text").textContent = text;
    root.querySelector('[data-action="expand"]').textContent = expanded ? "Collapse" : "Expand";
  }

  function handleBubbleClick(event) {
    const action = event.target?.dataset?.action;
    if (action === "dismiss" || action === "known") removeBubble();
    if (action === "expand") {
      expanded = !expanded;
      renderText(event.currentTarget);
    }
  }

  function tick() {
    const currentVideo = findVideo();
    const videoId = getVideoId();

    if (!currentVideo || !videoId) return;

    if (activeVideoId !== videoId) {
      activeVideoId = videoId;
      shownKeys = new Set();
      bubbles = [];
      transcriptSegments = [];
      lastCaptionText = "";
      expanded = false;
      removeBubble();
    }

    if (activeBubble) return;
    const activeSegment = transcriptSegments.find((segment) => {
      return currentVideo.currentTime >= segment.start_seconds && currentVideo.currentTime <= segment.end_seconds;
    });
    renderCaptionPanel(activeSegment?.text || readCaptionText());

    const dueBubble = bubbles.find((bubble) => {
      const key = `${videoId}:${bubble.concept}:${bubble.start_seconds}`;
      return !shownKeys.has(key) && currentVideo.currentTime >= bubble.start_seconds;
    });

    if (dueBubble) {
      shownKeys.add(`${videoId}:${dueBubble.concept}:${dueBubble.start_seconds}`);
      showBubble(dueBubble);
    }
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "contextbubble:analyze") return false;
    startAnalysis(message).then(sendResponse).catch((error) => {
      sendResponse({ error: error.message });
    });
    return true;
  });

  setInterval(tick, 500);
})();
