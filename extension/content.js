(function () {
  const SCRIPT_VERSION = 2;
  const API_BASE = "http://127.0.0.1:8000";
  const CAPTION_LOG_KEY = "contextbubbleCaptionLog";
  const STATUS_KEY = "contextbubbleStatus";
  const MAX_CAPTIONS = 120;
  const FALLBACK_CAPTION_INTERVAL_MS = 4500;
  const SAFE_SLOTS = ["top-right", "top-left", "middle-right", "middle-left", "bottom-right", "bottom-left"];
  const TOP_SLOTS = ["top-right", "top-left", "middle-right", "middle-left"];

  globalThis.__contextbubbleCleanup?.();
  globalThis.__contextbubbleVersion = SCRIPT_VERSION;

  let video;
  let bubbles = [];
  let transcriptSegments = [];
  let sentenceEntries = [];
  let shownKeys = new Set();
  let activeVideoId;
  let visibleBubbles = [];
  let trackedVideo;
  let lastCaptionText = "";
  let lastFallbackCaptionAt = 0;
  let loggedFallbackSegments = new Set();
  let lastVideoTime = 0;
  let analysisRunning = false;
  let pageGeneration = 0;
  let authToken = "";

  function getVideoId() {
    return new URLSearchParams(location.search).get("v") || "";
  }

  function findVideo() {
    video = document.querySelector("video");
    return video;
  }

  function findPlayer() {
    return document.querySelector(".html5-video-player") || findVideo()?.parentElement;
  }

  function clearVisibleBubbles() {
    for (const item of visibleBubbles) {
      clearTimeout(item.timer);
      item.root.remove();
    }
    visibleBubbles = [];
    document.getElementById("contextbubble-layer")?.remove();
  }

  function ensureLayer() {
    const player = findPlayer();
    if (!player) return null;
    if (getComputedStyle(player).position === "static") {
      player.style.position = "relative";
    }
    let layer = player.querySelector("#contextbubble-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "contextbubble-layer";
      player.appendChild(layer);
    }
    return layer;
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

  function appendCaptionLog(text, currentTime, segment, isFallback = false) {
    if (!text) {
      lastCaptionText = "";
      return;
    }

    if (isFallback) {
      const segmentKey = segment ? `${segment.start_seconds}:${segment.end_seconds}:${segment.text}` : text;
      if (loggedFallbackSegments.has(segmentKey)) return;
      if (Date.now() - lastFallbackCaptionAt < FALLBACK_CAPTION_INTERVAL_MS) return;
      loggedFallbackSegments.add(segmentKey);
      lastFallbackCaptionAt = Date.now();
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
    lastFallbackCaptionAt = 0;
    loggedFallbackSegments = new Set();
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

  function appendSentenceEntries(entries) {
    sentenceEntries = [...entries].sort((left, right) => left.start_seconds - right.start_seconds);
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
      "authorization": `Bearer ${authToken}`,
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

  function setSharedStatus(text) {
    chrome.storage.session.set({ [STATUS_KEY]: text });
  }

  function stageText(job) {
    const stages = {
      queued: "Queued...",
      fetching_captions: "Checking captions...",
      loading_demo: "Loading demo transcript...",
      fetching_metadata: "Reading video metadata...",
      downloading_audio: "Downloading audio...",
      normalizing_audio: "Normalizing audio...",
      transcribing: `Transcribing ${job.chunks_completed || 0} / ${job.chunks_total || 0} chunks...`,
      merging_transcript: "Merging transcript...",
      concept_agent: "Generating concepts...",
      reviewing: "Reviewing candidates...",
      validating: "Validating bubbles...",
      ready: `Ready: ${job.bubble_count || job.bubbles?.length || 0} bubbles.`,
      failed: job.message || "Preparation failed.",
    };
    return stages[job.stage] || `${job.stage || job.status}...`;
  }

  async function startPreparation(videoId, learnerLevel, demoMode, forceRefresh) {
    return fetchJson(`/api/videos/${videoId}/prepare`, {
      method: "POST",
      body: JSON.stringify({
        learner_level: learnerLevel,
        demo_mode: demoMode,
        force_refresh: forceRefresh,
      }),
    });
  }

  async function pollPreparation(job) {
    let lastUpdated = job.updated_at || "";
    let lastMove = Date.now();
    while (true) {
      setSharedStatus(stageText(job));
      if (job.status === "ready") return job;
      if (job.status === "failed") throw new Error(job.message || "Preparation failed.");
      await new Promise((resolve) => setTimeout(resolve, 1000));
      job = await fetchJson(`/api/preparations/${job.job_id}`);
      if (job.updated_at && job.updated_at !== lastUpdated) {
        lastUpdated = job.updated_at;
        lastMove = Date.now();
      }
      if (Date.now() - lastMove > 10 * 60 * 1000) {
        throw new Error("Preparation stalled.");
      }
    }
  }

  function captionsOrControlsVisible() {
    const player = findPlayer();
    return Boolean(readCaptionText()) || Boolean(player && !player.classList.contains("ytp-autohide"));
  }

  async function startAnalysis({ learnerLevel, sessionToken, demoMode = false, forceRefresh = false }) {
    if (analysisRunning) return { status: "already-running" };
    analysisRunning = true;
    authToken = sessionToken || "";

    const videoId = getVideoId();
    const currentVideo = findVideo();
    const requestGeneration = pageGeneration;
    try {
      if (!videoId) throw new Error("Open a YouTube watch page first.");
      if (!currentVideo) throw new Error("No YouTube video element found.");
      if (!authToken) throw new Error("Pair the backend first.");

      let job = await startPreparation(videoId, learnerLevel, demoMode, forceRefresh);
      job = await pollPreparation(job);
      if (getVideoId() !== videoId || findVideo() !== currentVideo || pageGeneration !== requestGeneration) {
        setSharedStatus("Analysis finished, but the page changed. Result discarded.");
        return { status: "stale-result-discarded", videoId };
      }
      transcriptSegments = [];
      sentenceEntries = [];
      bubbles = [];
      shownKeys = new Set();
      loggedFallbackSegments = new Set();
      lastFallbackCaptionAt = 0;
      clearVisibleBubbles();
      appendTranscriptSegments(job.segments || []);
      appendSentenceEntries(job.sentence_entries || []);
      appendBubbles(job.bubbles || []);

      return {
        videoId,
        count: bubbles.length,
        segmentCount: transcriptSegments.length,
        transcriptSource: job.transcript_source,
        jobId: job.job_id,
        analysisId: job.analysis_id,
      };
    } finally {
      analysisRunning = false;
    }
  }

  function removeBubble(root) {
    const item = visibleBubbles.find((entry) => entry.root === root);
    if (item) clearTimeout(item.timer);
    root.remove();
    visibleBubbles = visibleBubbles.filter((entry) => entry.root !== root);
  }

  function scheduleRemoval(item) {
    clearTimeout(item.timer);
    if (item.expanded) return;
    item.startedAt = Date.now();
    item.timer = setTimeout(() => removeBubble(item.root), item.remainingMs);
  }

  function pauseRemoval(item) {
    if (item.expanded) return;
    clearTimeout(item.timer);
    item.remainingMs = Math.max(0, item.remainingMs - (Date.now() - item.startedAt));
  }

  function availableSlot() {
    if (visibleBubbles.length >= 2) return "";
    const used = new Set(visibleBubbles.map((item) => item.slot));
    const slots = captionsOrControlsVisible() ? TOP_SLOTS : SAFE_SLOTS;
    return slots.find((slot) => !used.has(slot)) || "";
  }

  function showBubble(bubble) {
    const slot = availableSlot();
    const layer = ensureLayer();
    if (!slot || !layer) return false;

    const root = document.createElement("aside");
    root.className = `contextbubble-bubble contextbubble-slot-${slot}`;
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
    layer.appendChild(root);
    const item = { bubble, root, slot, expanded: false, timer: 0, remainingMs: 8000, startedAt: 0 };
    visibleBubbles.push(item);
    renderText(item);
    root.addEventListener("click", handleBubbleClick);
    root.addEventListener("mouseenter", () => pauseRemoval(item));
    root.addEventListener("mouseleave", () => scheduleRemoval(item));
    scheduleRemoval(item);
    return true;
  }

  function renderText(item) {
    const text = item.expanded
      ? `${item.bubble.short_explanation} ${item.bubble.expanded_explanation || ""}`
      : item.bubble.short_explanation;
    item.root.querySelector(".contextbubble-text").textContent = text;
    item.root.querySelector('[data-action="expand"]').textContent = item.expanded ? "Collapse" : "Expand";
  }

  function handleBubbleClick(event) {
    const action = event.target?.dataset?.action;
    const item = visibleBubbles.find((entry) => entry.root === event.currentTarget);
    if (!item) return;
    if (action === "dismiss" || action === "known") removeBubble(item.root);
    if (action === "expand") {
      item.expanded = !item.expanded;
      item.remainingMs = 8000;
      renderText(item);
      scheduleRemoval(item);
    }
  }

  function tick() {
    const currentVideo = findVideo();
    const videoId = getVideoId();

    if (!currentVideo || !videoId) return;
    if (trackedVideo !== currentVideo) {
      if (trackedVideo) trackedVideo.removeEventListener("seeking", clearVisibleBubbles);
      trackedVideo = currentVideo;
      trackedVideo.addEventListener("seeking", clearVisibleBubbles);
    }

    if (activeVideoId !== videoId) {
      activeVideoId = videoId;
      pageGeneration += 1;
      shownKeys = new Set();
      bubbles = [];
      transcriptSegments = [];
      sentenceEntries = [];
      lastCaptionText = "";
      lastFallbackCaptionAt = 0;
      loggedFallbackSegments = new Set();
      lastVideoTime = currentVideo.currentTime;
      clearCaptionLog();
      clearVisibleBubbles();
    }
    if (Math.abs(currentVideo.currentTime - lastVideoTime) > 2.5) {
      clearVisibleBubbles();
    }
    lastVideoTime = currentVideo.currentTime;

    const visibleCaptionText = readCaptionText();
    const captionEntries = sentenceEntries.length ? sentenceEntries : transcriptSegments;
    const activeSegment = captionEntries.find((segment) => {
      return currentVideo.currentTime >= segment.start_seconds && currentVideo.currentTime <= segment.end_seconds;
    });
    appendCaptionLog(visibleCaptionText || activeSegment?.text, currentVideo.currentTime, visibleCaptionText ? null : activeSegment, !visibleCaptionText);

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
      if (showBubble(dueBubble)) {
        shownKeys.add(`${videoId}:${dueBubble.concept}:${dueBubble.start_seconds}`);
      }
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
    if (trackedVideo) trackedVideo.removeEventListener("seeking", clearVisibleBubbles);
    clearVisibleBubbles();
    chrome.runtime.onMessage.removeListener(handleMessage);
  };
})();
