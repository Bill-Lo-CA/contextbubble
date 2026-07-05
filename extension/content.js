(function () {
  const SCRIPT_VERSION = 2;
  const BY_VIDEO_KEY = "contextbubbleByVideo";
  const ACTIVE_VIDEO_KEY = "contextbubbleActiveVideoId";
  const MAX_CAPTIONS = 120;
  const FALLBACK_CAPTION_INTERVAL_MS = 4500;

  globalThis.__contextbubbleCleanup?.();
  globalThis.__contextbubbleVersion = SCRIPT_VERSION;

  let video;
  let bubbles = [];
  let transcriptSegments = [];
  let sentenceEntries = [];
  let shownKeys = new Set();
  let activeVideoId;
  let trackedVideo;
  let lastCaptionText = "";
  let lastFallbackCaptionAt = 0;
  let loggedFallbackSegments = new Set();
  let lastVideoTime = 0;
  let analysisRunning = false;
  let pageGeneration = 0;
  let authToken = "";
  let storageQueue = Promise.resolve();
  let translationRequests = new Set();

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

  function readCaptionText() {
    return Array.from(document.querySelectorAll(".ytp-caption-segment"))
      .map((segment) => segment.textContent.trim())
      .filter(Boolean)
      .join(" ");
  }

  function normalizeText(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function formatTime(seconds) {
    seconds = Math.max(0, Math.round(seconds || 0));
    const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor(seconds % 3600 / 60)).padStart(2, "0");
    const secs = String(seconds % 60).padStart(2, "0");
    return `${hours}:${minutes}:${secs}`;
  }

  function appendCaptionLog(text, currentTime, segment, isFallback = false) {
    text = normalizeText(text);
    if (!text) {
      lastCaptionText = "";
      return;
    }

    const id = segment?.id || `live-${Math.max(0, Math.round(currentTime))}-${text.slice(0, 20)}`;
    if (isFallback) {
      const segmentKey = segment ? segment.id || `${segment.start_seconds}:${segment.end_seconds}:${segment.text}` : text;
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
      updateVideoState(getVideoId(), (state) => {
        const log = state.captionLog || [];
        const index = log.findIndex((entry) => entry.id === id);
        const entry = {
          ...(index >= 0 ? log[index] : {}),
          id,
          timeText,
          text,
          source_text: text,
          start_seconds: segment?.start_seconds ?? currentTime,
          end_seconds: segment?.end_seconds ?? currentTime,
          translation_status: segment?.id ? "pending" : "skipped",
          savedAt: Date.now(),
        };
        if (index >= 0) {
          log[index] = entry;
        } else {
          log.push(entry);
        }
        state.captionLog = log.slice(-MAX_CAPTIONS);
      });
      if (segment?.id) requestTranslation(segment, text);
    }
  }

  async function requestTranslation(segment, sourceText) {
    if (translationRequests.has(segment.id) || !authToken) return;
    translationRequests.add(segment.id);
    try {
      const result = await fetchJson("/api/translations", {
        method: "POST",
        body: JSON.stringify({
          id: segment.id,
          source_text: sourceText,
          context_before: nearbyText(segment.start_seconds, -1),
          context_after: nearbyText(segment.end_seconds, 1),
          target_language: "zh-TW",
        }),
      });
      await updateCaptionTranslation(getVideoId(), result);
    } catch (error) {
      await updateCaptionTranslation(getVideoId(), {
        id: segment.id,
        status: "failed",
        translated_text: "",
        reason: error.message,
      });
    }
  }

  async function requestSentenceTranslation(entry) {
    if (translationRequests.has(entry.id) || !authToken) return;
    translationRequests.add(entry.id);
    await updateSentenceTranslation(getVideoId(), {
      id: entry.id,
      status: "pending",
      translated_text: "",
      reason: "",
    });
    try {
      const result = await fetchJson("/api/translations", {
        method: "POST",
        body: JSON.stringify({
          id: entry.id,
          source_text: entry.text,
          context_before: nearbySentenceText(entry.start_seconds, -1),
          context_after: nearbySentenceText(entry.end_seconds, 1),
          target_language: "zh-TW",
        }),
      });
      await updateSentenceTranslation(getVideoId(), result);
    } catch (error) {
      await updateSentenceTranslation(getVideoId(), {
        id: entry.id,
        status: "failed",
        translated_text: "",
        reason: error.message,
      });
    }
  }

  function nearbyText(seconds, direction) {
    const sorted = [...transcriptSegments].sort((left, right) => left.start_seconds - right.start_seconds);
    const index = sorted.findIndex((segment) => {
      return seconds >= segment.start_seconds && seconds <= segment.end_seconds;
    });
    const neighbor = sorted[index + direction];
    return neighbor?.text || "";
  }

  function nearbySentenceText(seconds, direction) {
    const sorted = [...sentenceEntries].sort((left, right) => left.start_seconds - right.start_seconds);
    const index = sorted.findIndex((entry) => {
      return seconds >= entry.start_seconds && seconds <= entry.end_seconds;
    });
    const neighbor = sorted[index + direction];
    return neighbor?.text || "";
  }

  function updateCaptionTranslation(videoId, result) {
    return updateVideoState(videoId, (state) => {
      const log = state.captionLog || [];
      const entry = log.find((item) => item.id === result.id);
      if (!entry) return;
      entry.translated_text = normalizeText(result.translated_text);
      entry.translation_status = result.status || "translated";
      entry.translation_reason = result.reason || "";
    });
  }

  function updateSentenceTranslation(videoId, result) {
    return updateVideoState(videoId, (state) => {
      const entries = state.sentenceEntries || [];
      const entry = entries.find((item) => item.id === result.id);
      if (!entry) return;
      entry.translated_text = normalizeText(result.translated_text);
      entry.translation_status = result.status || "translated";
      entry.translation_reason = result.reason || "";
      sentenceEntries = entries;
    });
  }

  function clearCaptionLog() {
    lastCaptionText = "";
    lastFallbackCaptionAt = 0;
    loggedFallbackSegments = new Set();
    updateVideoState(getVideoId(), (state) => {
      state.captionLog = [];
    });
  }

  function updateVideoState(videoId, mutate) {
    if (!videoId) return Promise.resolve();
    storageQueue = storageQueue.then(() => new Promise((resolve) => {
      chrome.storage.local.get(BY_VIDEO_KEY, (saved) => {
        const byVideo = saved[BY_VIDEO_KEY] || {};
        const state = byVideo[videoId] || {};
        mutate(state);
        state.updatedAt = Date.now();
        byVideo[videoId] = state;
        chrome.storage.local.set({ [BY_VIDEO_KEY]: byVideo }, resolve);
      });
    }));
    storageQueue = storageQueue.catch((error) => {
      console.warn("[ContextBubble] storage update failed", error);
    });
    return storageQueue;
  }

  function setActiveVideo(videoId) {
    if (!videoId) return;
    chrome.storage.local.set({ [ACTIVE_VIDEO_KEY]: videoId });
  }

  async function resetStoredCaptions(videoId) {
    lastCaptionText = "";
    lastFallbackCaptionAt = 0;
    loggedFallbackSegments = new Set();
    await updateVideoState(videoId, (state) => {
      state.captionLog = [];
      state.sentenceEntries = [];
      state.status = "Re-analyzing...";
    });
  }

  function appendTranscriptSegments(segments) {
    const existing = new Set(transcriptSegments.map((segment) => {
      return `${segment.start_seconds}:${segment.end_seconds}:${segment.text}`;
    }));
    for (const segment of segments) {
      const key = `${segment.start_seconds}:${segment.end_seconds}:${segment.text}`;
      if (!existing.has(key)) {
        existing.add(key);
        transcriptSegments.push({ ...segment, text: normalizeText(segment.text) });
      }
    }
    transcriptSegments.sort((left, right) => left.start_seconds - right.start_seconds);
  }

  function appendSentenceEntries(entries) {
    sentenceEntries = entries
      .map((entry) => ({ ...entry, text: normalizeText(entry.text) }))
      .sort((left, right) => left.start_seconds - right.start_seconds);
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
    return globalThis.contextbubbleBackend.fetchJson(path, { ...options, headers: authHeaders() });
  }

  function setSharedStatus(text) {
    updateVideoState(getVideoId(), (state) => {
      state.status = text;
    });
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
      appendTranscriptSegments(job.segments || []);
      if (job.status === "ready") return job;
      if (job.status === "failed") throw new Error(job.message || "Preparation failed.");
      await new Promise((resolve) => setTimeout(resolve, 1000));
      job = await fetchJson(`/api/preparations/${job.job_id}?include_transcript=true`);
      if (job.updated_at && job.updated_at !== lastUpdated) {
        lastUpdated = job.updated_at;
        lastMove = Date.now();
      }
      const stallTimeout = job.stage === "transcribing" ? 45 * 60 * 1000 : 10 * 60 * 1000;
      if (Date.now() - lastMove > stallTimeout) {
        throw new Error("Preparation stalled.");
      }
    }
  }

  function captionsOrControlsVisible() {
    const player = findPlayer();
    return Boolean(readCaptionText()) || Boolean(player && !player.classList.contains("ytp-autohide"));
  }

  const overlay = globalThis.contextbubbleOverlay.create({
    findPlayer,
    captionsOrControlsVisible,
  });

  function enterVideo(videoId, currentVideo) {
    if (activeVideoId === videoId) return;
    activeVideoId = videoId;
    setActiveVideo(videoId);
    pageGeneration += 1;
    shownKeys = new Set();
    bubbles = [];
    transcriptSegments = [];
    sentenceEntries = [];
    translationRequests = new Set();
    lastCaptionText = "";
    lastFallbackCaptionAt = 0;
    loggedFallbackSegments = new Set();
    lastVideoTime = currentVideo?.currentTime || 0;
    overlay.clear();
  }

  async function startAnalysis({ learnerLevel, sessionToken, demoMode = false, forceRefresh = false }) {
    if (analysisRunning) return { status: "already-running" };
    analysisRunning = true;
    authToken = sessionToken || "";

    const videoId = getVideoId();
    const currentVideo = findVideo();
    try {
      if (!videoId) throw new Error("Open a YouTube watch page first.");
      if (!currentVideo) throw new Error("No YouTube video element found.");
      if (!authToken) throw new Error("Pair the backend first.");
      enterVideo(videoId, currentVideo);
      if (forceRefresh) await resetStoredCaptions(videoId);
      const requestGeneration = pageGeneration;

      let job = await startPreparation(videoId, learnerLevel, demoMode, forceRefresh);
      job = await pollPreparation(job);
      job = await fetchJson(`/api/preparations/${job.job_id}?include_transcript=true&include_sentence_entries=true`);
      if (getVideoId() !== videoId || findVideo() !== currentVideo || pageGeneration !== requestGeneration) {
        setSharedStatus("Analysis finished, but the page changed. Result discarded.");
        return { status: "stale-result-discarded", videoId };
      }
      transcriptSegments = [];
      sentenceEntries = [];
      bubbles = [];
      translationRequests = new Set();
      shownKeys = new Set();
      loggedFallbackSegments = new Set();
      lastFallbackCaptionAt = 0;
      overlay.clear();
      appendTranscriptSegments(job.segments || []);
      appendSentenceEntries(job.sentence_entries || []);
      appendBubbles(job.bubbles || []);
      await updateVideoState(videoId, (state) => {
        state.jobId = job.job_id;
        state.status = "Ready.";
        state.sentenceEntries = sentenceEntries;
        state.captionLog = state.captionLog || [];
      });

      return {
        videoId,
        count: bubbles.length,
        segmentCount: transcriptSegments.length,
        sentenceCount: sentenceEntries.length,
        transcriptSource: job.transcript_source,
        jobId: job.job_id,
        analysisId: job.analysis_id,
      };
    } finally {
      analysisRunning = false;
    }
  }

  function tick() {
    const currentVideo = findVideo();
    const videoId = getVideoId();

    if (!currentVideo || !videoId) return;
    if (trackedVideo !== currentVideo) {
      if (trackedVideo) trackedVideo.removeEventListener("seeking", overlay.clear);
      trackedVideo = currentVideo;
      trackedVideo.addEventListener("seeking", overlay.clear);
    }

    if (activeVideoId !== videoId) {
      enterVideo(videoId, currentVideo);
    }
    if (Math.abs(currentVideo.currentTime - lastVideoTime) > 2.5) {
      overlay.clear();
    }
    lastVideoTime = currentVideo.currentTime;

    const activeSentence = sentenceEntries.find((entry) => {
      return currentVideo.currentTime >= entry.start_seconds && currentVideo.currentTime <= entry.end_seconds;
    });
    if (activeSentence) requestSentenceTranslation(activeSentence);

    const visibleCaptionText = readCaptionText();
    const activeSegment = transcriptSegments.find((segment) => {
      return currentVideo.currentTime >= segment.start_seconds && currentVideo.currentTime <= segment.end_seconds;
    });
    if (!sentenceEntries.length) {
      appendCaptionLog(visibleCaptionText || activeSegment?.text, currentVideo.currentTime, activeSegment, !visibleCaptionText);
    }

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
      if (overlay.show(dueBubble)) {
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
    if (trackedVideo) trackedVideo.removeEventListener("seeking", overlay.clear);
    overlay.clear();
    chrome.runtime.onMessage.removeListener(handleMessage);
  };
})();
