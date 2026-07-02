(function () {
  const SCRIPT_VERSION = 2;
  const API_BASE = "http://127.0.0.1:8000";
  const CHUNK_SECONDS = 30;
  const FOLLOWUP_CHUNKS = 1;
  const CAPTION_LOG_KEY = "contextbubbleCaptionLog";
  const MAX_CAPTIONS = 120;

  globalThis.__contextbubbleCleanup?.();
  globalThis.__contextbubbleVersion = SCRIPT_VERSION;

  let video;
  let bubbles = [];
  let transcriptSegments = [];
  let shownKeys = new Set();
  let activeVideoId;
  let activeBubble;
  let hideTimer;
  let lastCaptionText = "";
  let expanded = false;
  let analysisRunning = false;
  let apiToken = "";

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

  function formatTime(seconds) {
    seconds = Math.max(0, Math.round(seconds || 0));
    const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor(seconds % 3600 / 60)).padStart(2, "0");
    const secs = String(seconds % 60).padStart(2, "0");
    return `${hours}:${minutes}:${secs}`;
  }

  function appendCaptionLog(text, currentTime, segment) {
    if (!text) {
      lastCaptionText = "";
      return;
    }

    const timeText = segment
      ? `video ${formatTime(currentTime)} · segment ${formatTime(segment.start_seconds)}-${formatTime(segment.end_seconds)}`
      : `video ${formatTime(currentTime)}`;
    const captionKey = segment
      ? `${segment.start_seconds}:${segment.end_seconds}:${text}`
      : text;

    if (captionKey !== lastCaptionText) {
      lastCaptionText = captionKey;
      chrome.storage.local.get(CAPTION_LOG_KEY, (saved) => {
        const log = saved[CAPTION_LOG_KEY] || [];
        log.push({ timeText, text, savedAt: Date.now() });
        chrome.storage.local.set({ [CAPTION_LOG_KEY]: log.slice(-MAX_CAPTIONS) });
      });
    }
  }

  function clearCaptionLog() {
    lastCaptionText = "";
    chrome.storage.local.set({ [CAPTION_LOG_KEY]: [] });
  }

  function appendTranscriptSegments(segments) {
    const existing = new Set(transcriptSegments.map((segment) => {
      return `${segment.start_seconds}:${segment.end_seconds}:${segment.text}`;
    }));
    for (const segment of segments) {
      const key = `${segment.start_seconds}:${segment.end_seconds}:${segment.text}`;
      if (!existing.has(key)) {
        existing.add(key);
        transcriptSegments.push(segment);
      }
    }
    transcriptSegments.sort((left, right) => left.start_seconds - right.start_seconds);
  }

  function appendBubbles(nextBubbles) {
    const existing = new Set(bubbles.map((bubble) => {
      return `${bubble.concept}:${bubble.start_seconds}`;
    }));
    for (const bubble of nextBubbles) {
      const key = `${bubble.concept}:${bubble.start_seconds}`;
      if (!existing.has(key)) {
        existing.add(key);
        bubbles.push(bubble);
      }
    }
    bubbles.sort((left, right) => left.start_seconds - right.start_seconds);
  }

  function authHeaders() {
    return {
      "authorization": `Bearer ${apiToken}`,
      "content-type": "application/json",
    };
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: authHeaders(),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Backend request failed.");
    return result;
  }

  async function fetchDemoTranscript(videoId) {
    return fetchJson("/api/demo-transcript", {
      method: "POST",
      body: JSON.stringify({
        video_id: videoId,
      }),
    });
  }

  async function fetchYoutubeTranscript(videoId, currentTime) {
    return fetchJson("/api/youtube-subtitles", {
      method: "POST",
      body: JSON.stringify({
        video_id: videoId,
        current_time: currentTime,
        chunk_seconds: CHUNK_SECONDS,
      }),
    });
  }

  async function startBackendAnalysis(videoId, transcriptId, learnerLevel) {
    return fetchJson("/api/analyses", {
      method: "POST",
      body: JSON.stringify({
        video_id: videoId,
        transcript_id: transcriptId,
        learner_level: learnerLevel,
        force_refresh: false,
      }),
    });
  }

  async function pollAnalysis(analysisId) {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      const result = await fetchJson(`/api/analyses/${analysisId}`);
      if (result.status === "completed") return result;
      if (result.status === "failed") throw new Error(result.message || "Analysis failed.");
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
    throw new Error("Analysis timed out.");
  }

  async function startAnalysis({ learnerLevel, apiToken: token }) {
    if (analysisRunning) return { status: "already-running" };
    analysisRunning = true;
    apiToken = token || "";

    const videoId = getVideoId();
    const currentVideo = findVideo();
    try {
      if (!videoId) throw new Error("Open a YouTube watch page first.");
      if (!currentVideo) throw new Error("No YouTube video element found.");
      if (!apiToken) throw new Error("Missing API token.");

      transcriptSegments = [];
      bubbles = [];
      shownKeys = new Set();
      clearCaptionLog();
      removeBubble();

      let transcript;
      try {
        transcript = await fetchYoutubeTranscript(videoId, currentVideo.currentTime || 0);
      } catch (_error) {
        transcript = await fetchDemoTranscript(videoId);
      }
      appendTranscriptSegments(transcript.segments || []);
      const started = await startBackendAnalysis(videoId, transcript.transcript_id, learnerLevel);
      const result = await pollAnalysis(started.analysis_id);
      appendBubbles(result.bubbles || []);

      return {
        videoId,
        count: bubbles.length,
        segmentCount: transcriptSegments.length,
        analysisId: started.analysis_id,
      };
    } finally {
      analysisRunning = false;
    }
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
      clearCaptionLog();
      removeBubble();
    }

    const activeSegment = transcriptSegments.find((segment) => {
      return currentVideo.currentTime >= segment.start_seconds && currentVideo.currentTime <= segment.end_seconds;
    });
    appendCaptionLog(activeSegment?.text || readCaptionText(), currentVideo.currentTime, activeSegment);

    if (activeBubble) return;
    for (const bubble of bubbles) {
      const key = `${videoId}:${bubble.concept}:${bubble.start_seconds}`;
      if (!shownKeys.has(key) && currentVideo.currentTime > bubble.start_seconds + 1.5) {
        shownKeys.add(key);
      }
    }

    const dueBubble = bubbles.find((bubble) => {
      const key = `${videoId}:${bubble.concept}:${bubble.start_seconds}`;
      return !shownKeys.has(key)
        && currentVideo.currentTime >= bubble.start_seconds - 0.3
        && currentVideo.currentTime <= bubble.start_seconds + 1.5;
    });

    if (dueBubble) {
      shownKeys.add(`${videoId}:${dueBubble.concept}:${dueBubble.start_seconds}`);
      showBubble(dueBubble);
    }
  }

  function handleMessage(message, _sender, sendResponse) {
    if (message?.type !== "contextbubble:analyze-v2") return false;
    startAnalysis(message).then(sendResponse).catch((error) => {
      sendResponse({ error: error.message });
    });
    return true;
  }

  chrome.runtime.onMessage.addListener(handleMessage);
  const tickTimer = setInterval(tick, 500);
  globalThis.__contextbubbleCleanup = () => {
    clearInterval(tickTimer);
    chrome.runtime.onMessage.removeListener(handleMessage);
  };
})();
