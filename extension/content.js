(function () {
  const SCRIPT_VERSION = 2;
  const API_BASE = "http://127.0.0.1:8000";
  const CHUNK_SECONDS = 30;
  const FOLLOWUP_CHUNKS = 1;
  const CAPTION_ID = "contextbubble-caption-v2";

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

  function formatSeconds(seconds) {
    return `${Math.round(seconds)}s`;
  }

  function renderCaptionPanel(text, currentTime, segment) {
    document.getElementById("contextbubble-caption")?.remove();
    let panel = document.getElementById(CAPTION_ID);
    if (!text) {
      panel?.remove();
      lastCaptionText = "";
      return;
    }

    if (!panel) {
      panel = document.createElement("div");
      panel.id = CAPTION_ID;
      document.body.appendChild(panel);
    }

    const timeText = segment
      ? `video ${formatSeconds(currentTime)} · segment ${formatSeconds(segment.start_seconds)}-${formatSeconds(segment.end_seconds)}`
      : `video ${formatSeconds(currentTime)}`;
    const panelText = `${timeText}\n${text}`;

    if (panelText !== lastCaptionText) {
      lastCaptionText = panelText;
      panel.textContent = "";
      const time = document.createElement("div");
      time.className = "contextbubble-caption-time";
      time.textContent = timeText;
      const body = document.createElement("div");
      body.textContent = text;
      panel.append(time, body);
    }
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

  async function fetchYoutubeTranscript(videoId, currentVideo, currentTime) {
    const response = await fetch(`${API_BASE}/api/youtube-subtitles`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        video_id: videoId,
        current_time: currentTime,
        playback_rate: currentVideo.playbackRate || 1,
        chunk_seconds: CHUNK_SECONDS,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Subtitle retrieval failed.");
    return result;
  }

  async function analyzeChunkAt(videoId, currentVideo, learnerLevel, currentTime) {
    const transcript = await fetchYoutubeTranscript(videoId, currentVideo, currentTime);
    const receivedAt = currentVideo.currentTime;
    appendTranscriptSegments(transcript.segments || []);

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
    appendBubbles(result.bubbles || []);
    return {
      requestedAt: transcript.request_time_seconds,
      receivedAt,
      respondedAt: findVideo()?.currentTime ?? receivedAt,
      chunkStart: transcript.chunk_start_seconds,
      chunkEnd: transcript.chunk_end_seconds,
      segmentCount: transcript.segments?.length || 0,
      bubbleCount: result.bubbles?.length || 0,
    };
  }

  async function startAnalysis({ learnerLevel }) {
    if (analysisRunning) return { status: "already-running" };
    analysisRunning = true;

    const videoId = getVideoId();
    const currentVideo = findVideo();
    try {
      if (!videoId) throw new Error("Open a YouTube watch page first.");
      if (!currentVideo) throw new Error("No YouTube video element found.");

      transcriptSegments = [];
      bubbles = [];
      shownKeys = new Set();
      removeBubble();

      const analyzed = [];
      let currentTime = currentVideo.currentTime;
      for (let index = 0; index <= FOLLOWUP_CHUNKS; index += 1) {
        const chunk = await analyzeChunkAt(videoId, currentVideo, learnerLevel, currentTime);
        analyzed.push(chunk);
        currentTime = chunk.chunkEnd + 0.1;
      }

      const latest = analyzed.at(-1);
      return {
        videoId,
        count: bubbles.length,
        chunksAnalyzed: analyzed.length,
        segmentCount: transcriptSegments.length,
        requestedAt: analyzed[0]?.requestedAt,
        receivedAt: latest?.receivedAt,
        respondedAt: latest?.respondedAt,
        chunkStart: analyzed[0]?.chunkStart,
        chunkEnd: latest?.chunkEnd,
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
      removeBubble();
    }

    if (activeBubble) return;
    const activeSegment = transcriptSegments.find((segment) => {
      return currentVideo.currentTime >= segment.start_seconds && currentVideo.currentTime <= segment.end_seconds;
    });
    renderCaptionPanel(activeSegment?.text || readCaptionText(), currentVideo.currentTime, activeSegment);

    const dueBubble = bubbles.find((bubble) => {
      const key = `${videoId}:${bubble.concept}:${bubble.start_seconds}`;
      return !shownKeys.has(key) && currentVideo.currentTime >= bubble.start_seconds;
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
