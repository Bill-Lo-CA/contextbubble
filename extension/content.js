(function () {
  const SCRIPT_VERSION = 2;
  const BY_VIDEO_KEY = "contextbubbleByVideo";
  const ACTIVE_VIDEO_KEY = "contextbubbleActiveVideoId";
  const OWNER_KEY = "contextbubbleAnalysisOwner";
  const OWNER_SESSION_KEY = "contextbubbleAnalysisOwner";
  const MAX_CAPTIONS = 120;
  const FALLBACK_CAPTION_INTERVAL_MS = 4500;
  const TRANSLATION_RETRY_DELAY_MS = 30000;
  const TRANSLATION_LOOKAHEAD_SECONDS = 45;
  const TRANSLATION_LOOKAHEAD_BATCH = 4;
  const PREPARED_TRANSCRIPT_FETCH_INTERVAL_MS = 3000;
  const TRANSLATION_JOB_POLL_INTERVAL_MS = 1000;

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
  let analysisOwner = null;
  let translationForceRefresh = false;
  let inFlightTranslations = new Set();
  let completedTranslations = new Set();
  let failedTranslations = new Map();
  let sentenceTranslationResults = new Map();
  let preparedTranscriptFetch = null;
  let lastPreparedTranscriptFetchAt = 0;
  let contextInvalidated = false;
  let tickTimer;

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

  function resetTranslationState() {
    inFlightTranslations = new Set();
    completedTranslations = new Set();
    failedTranslations = new Map();
    sentenceTranslationResults = new Map();
  }

  function sameOwner(left, right) {
    return Boolean(left && right
      && left.tabId === right.tabId
      && left.videoId === right.videoId
      && left.generation === right.generation);
  }

  function readSessionOwner() {
    try {
      return JSON.parse(sessionStorage.getItem(OWNER_SESSION_KEY) || "null");
    } catch {
      return null;
    }
  }

  function writeSessionOwner(owner) {
    try {
      sessionStorage.setItem(OWNER_SESSION_KEY, JSON.stringify(owner));
    } catch {
      // Storage can be blocked in unusual browser modes; local owner still works until reload.
    }
  }

  function clearSessionOwner() {
    try {
      sessionStorage.removeItem(OWNER_SESSION_KEY);
    } catch {
      // Ignore storage failures during cleanup.
    }
  }

  function ownsVideo(videoId) {
    return Boolean(analysisOwner && analysisOwner.videoId === videoId);
  }

  function ownerMatches(owner, videoId) {
    return sameOwner(owner, analysisOwner) && owner.videoId === videoId;
  }

  function readLocal(keys) {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get(keys, resolve);
      } catch (error) {
        stopIfContextInvalidated(error);
        resolve({});
      }
    });
  }

  function writeLocal(values) {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.set(values, resolve);
      } catch (error) {
        stopIfContextInvalidated(error);
        resolve();
      }
    });
  }

  async function storedOwnerMatches(videoId) {
    const saved = await readLocal(OWNER_KEY);
    return ownerMatches(saved[OWNER_KEY], videoId);
  }

  async function claimAnalysisOwner(tabId, videoId, forceRefresh) {
    analysisOwner = {
      tabId,
      videoId,
      generation: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      startedAt: Date.now(),
      forceRefresh: Boolean(forceRefresh),
      jobId: "",
    };
    await writeLocal({
      [OWNER_KEY]: analysisOwner,
      [ACTIVE_VIDEO_KEY]: videoId,
    });
    writeSessionOwner(analysisOwner);
    return analysisOwner;
  }

  async function restoreAnalysisOwner() {
    const videoId = getVideoId();
    const sessionOwner = readSessionOwner();
    if (!videoId || !sessionOwner || sessionOwner.videoId !== videoId) return false;
    const saved = await readLocal([OWNER_KEY, BY_VIDEO_KEY]);
    if (!sameOwner(saved[OWNER_KEY], sessionOwner)) return false;
    const state = saved[BY_VIDEO_KEY]?.[videoId] || {};
    analysisOwner = {
      ...saved[OWNER_KEY],
      jobId: saved[OWNER_KEY].jobId || sessionOwner.jobId || state.jobId || "",
    };
    translationForceRefresh = Boolean(sessionOwner.forceRefresh);
    writeSessionOwner(analysisOwner);
    return true;
  }

  async function rememberPreparationJob(videoId, job) {
    if (!job?.job_id || !analysisOwner) return;
    analysisOwner = { ...analysisOwner, jobId: job.job_id };
    writeSessionOwner(analysisOwner);
    await writeLocal({ [OWNER_KEY]: analysisOwner });
    await updateVideoState(videoId, (state) => {
      state.jobId = job.job_id;
    });
  }

  function formatTime(seconds) {
    seconds = Math.max(0, Math.round(seconds || 0));
    const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor(seconds % 3600 / 60)).padStart(2, "0");
    const secs = String(seconds % 60).padStart(2, "0");
    return `${hours}:${minutes}:${secs}`;
  }

  function appendCaptionLog(videoId, text, currentTime, segment, isFallback = false) {
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
      updateVideoState(videoId, (state) => {
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
      if (segment?.id) requestTranslation(videoId, segment, text);
    }
  }

  async function requestTranslation(videoId, segment, sourceText) {
    const requestKey = `${segment.id}:${normalizeText(sourceText)}`;
    return enqueueTranslation(requestKey, videoId, async () => {
      try {
        const result = await requestQueuedTranslation({
          id: segment.id,
          source_text: sourceText,
          context_before: nearbyText(segment.start_seconds, -1),
          context_after: nearbyText(segment.end_seconds, 1),
          target_language: "zh-TW",
          force_refresh: translationForceRefresh,
        });
        await updateCaptionTranslation(videoId, result);
        return result;
      } catch (error) {
        await updateCaptionTranslation(videoId, {
          id: segment.id,
          status: "failed",
          translated_text: "",
          reason: error.message,
        });
        throw error;
      }
    });
  }

  function translationRetryBlocked(requestKey) {
    const failedAt = failedTranslations.get(requestKey);
    return Boolean(failedAt && Date.now() - failedAt < TRANSLATION_RETRY_DELAY_MS);
  }

  function canQueueTranslation(requestKey, videoId) {
    return authToken
      && ownsVideo(videoId)
      && !inFlightTranslations.has(requestKey)
      && !completedTranslations.has(requestKey)
      && !translationRetryBlocked(requestKey);
  }

  function enqueueTranslation(requestKey, videoId, task) {
    if (!canQueueTranslation(requestKey, videoId)) return Promise.resolve();

    inFlightTranslations.add(requestKey);
    const queued = (async () => {
      try {
        if (!await storedOwnerMatches(videoId)) return;
        const result = await task();
        if (result?.status === "translated" || result?.decision === "skip") {
          completedTranslations.add(requestKey);
          failedTranslations.delete(requestKey);
        } else {
          failedTranslations.set(requestKey, Date.now());
        }
      } catch {
        failedTranslations.set(requestKey, Date.now());
      } finally {
        inFlightTranslations.delete(requestKey);
      }
    });
    return queued;
  }

  function translationDone(result) {
    return ["translated", "failed", "skipped"].includes(result?.status);
  }

  async function requestQueuedTranslation(payload) {
    let result = await fetchJson("/api/translations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    while (result.translation_job_id && !translationDone(result)) {
      await new Promise((resolve) => setTimeout(resolve, TRANSLATION_JOB_POLL_INTERVAL_MS));
      result = await fetchJson(`/api/translations/${result.translation_job_id}`);
    }
    return result;
  }

  async function requestSentenceTranslation(videoId, entry) {
    const requestKey = sentenceTranslationKey(entry);
    return enqueueTranslation(requestKey, videoId, async () => {
      await updateSentenceTranslation(videoId, {
        id: entry.id,
        status: "pending",
        translated_text: "",
        reason: "",
      }, entry.text);
      try {
        const result = await requestQueuedTranslation({
          id: entry.id,
          source_text: entry.text,
          context_before: nearbySentenceText(entry.start_seconds, -1),
          context_after: nearbySentenceText(entry.end_seconds, 1),
          target_language: "zh-TW",
          force_refresh: translationForceRefresh,
        });
        sentenceTranslationResults.set(requestKey, result);
        await updateSentenceTranslation(videoId, result, entry.text);
        return result;
      } catch (error) {
        await updateSentenceTranslation(videoId, {
          id: entry.id,
          status: "failed",
          translated_text: "",
          reason: error.message,
        }, entry.text);
        throw error;
      }
    });
  }

  function sentenceTranslationKey(entry) {
    return `${entry.id}:${normalizeText(entry.text)}`;
  }

  function queueSentenceTranslationLookahead(videoId, currentTime) {
    const endTime = currentTime + TRANSLATION_LOOKAHEAD_SECONDS;
    sentenceEntries
      .filter((entry) => entry.end_seconds >= currentTime - 1 && entry.start_seconds <= endTime)
      .filter((entry) => canQueueTranslation(sentenceTranslationKey(entry), videoId))
      .slice(0, TRANSLATION_LOOKAHEAD_BATCH)
      .forEach((entry) => requestSentenceTranslation(videoId, entry));
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

  function updateSentenceTranslation(videoId, result, expectedText = "") {
    return updateVideoState(videoId, (state) => {
      const applyTranslation = (entries) => {
        const entry = entries.find((item) => item.id === result.id);
        if (!entry) return false;
        if (expectedText && normalizeText(entry.text) !== normalizeText(expectedText)) return false;
        entry.translated_text = normalizeText(result.translated_text);
        entry.translation_status = result.status || "translated";
        entry.translation_reason = result.reason || "";
        return true;
      };
      const shownEntries = state.shownSentenceEntries || [];
      const allEntries = state.allSentenceEntries || [];
      const updatedShown = applyTranslation(shownEntries);
      const updatedAll = applyTranslation(allEntries);
      if (updatedShown) state.shownSentenceEntries = shownEntries;
      if (updatedAll) state.allSentenceEntries = allEntries;
      return updatedShown || updatedAll;
    });
  }

  function showSentenceEntry(videoId, entry) {
    if (!entry) return Promise.resolve();
    return updateVideoState(videoId, (state) => {
      const entries = state.shownSentenceEntries || [];
      const existing = entries.find((item) => item.id === entry.id);
      const next = {
        ...(existing || {}),
        ...entry,
        text: normalizeText(entry.text),
        source_text: normalizeText(entry.source_text || entry.text),
      };
      if (existing && normalizeText(existing.text) !== next.text) {
        next.translated_text = "";
        next.translation_status = "";
        next.translation_reason = "";
      }
      const translation = sentenceTranslationResults.get(sentenceTranslationKey(next));
      if (translation) {
        next.translated_text = normalizeText(translation.translated_text);
        next.translation_status = translation.status || "translated";
        next.translation_reason = translation.reason || "";
      } else if (inFlightTranslations.has(sentenceTranslationKey(next)) && !next.translated_text) {
        next.translation_status = "pending";
      }
      if (existing) {
        entries[entries.indexOf(existing)] = next;
      } else {
        entries.push(next);
      }
      state.shownSentenceEntries = entries.sort((left, right) => left.start_seconds - right.start_seconds);
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

  function stopIfContextInvalidated(error) {
    const message = String(error?.message || error || "");
    if (!message.includes("Extension context invalidated")) return false;
    contextInvalidated = true;
    if (tickTimer) clearInterval(tickTimer);
    return true;
  }

  function updateVideoState(videoId, mutate) {
    if (!videoId || contextInvalidated) return Promise.resolve();
    storageQueue = storageQueue.then(() => new Promise((resolve) => {
      try {
        chrome.storage.local.get([BY_VIDEO_KEY, OWNER_KEY], (saved) => {
          try {
            if (!ownerMatches(saved[OWNER_KEY], videoId)) {
              resolve(false);
              return;
            }
            const byVideo = saved[BY_VIDEO_KEY] || {};
            const state = byVideo[videoId] || {};
            const result = mutate(state);
            state.updatedAt = Date.now();
            byVideo[videoId] = state;
            chrome.storage.local.set({ [BY_VIDEO_KEY]: byVideo }, () => {
              resolve(result === undefined ? true : result);
            });
          } catch (error) {
            stopIfContextInvalidated(error);
            resolve(false);
          }
        });
      } catch (error) {
        stopIfContextInvalidated(error);
        resolve(false);
      }
    }));
    storageQueue = storageQueue.catch((error) => {
      if (!stopIfContextInvalidated(error)) {
        console.warn("[ContextBubble] storage update failed", error);
      }
    });
    return storageQueue;
  }

  function resetTimelineDisplay(videoId, status = "") {
    lastCaptionText = "";
    lastFallbackCaptionAt = 0;
    loggedFallbackSegments = new Set();
    return updateVideoState(videoId, (state) => {
      state.captionLog = [];
      state.shownSentenceEntries = [];
      state.sentenceEntries = [];
      if (status) state.status = status;
    });
  }

  async function resetStoredCaptions(videoId) {
    await resetTimelineDisplay(videoId, "Re-analyzing...");
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
    sentenceEntries = normalizedSentenceEntries(entries);
  }

  function normalizedSentenceEntries(entries) {
    return entries
      .map((entry) => ({ ...entry, text: normalizeText(entry.text) }))
      .sort((left, right) => left.start_seconds - right.start_seconds);
  }

  function replacementSentenceEntry(entry, byId, entries) {
    const matched = byId.get(entry.id);
    if (matched) return matched;
    const start = Number(entry.start_seconds || 0);
    const end = Number(entry.end_seconds || start);
    return entries.find((candidate) => {
      const candidateStart = Number(candidate.start_seconds || 0);
      const candidateEnd = Number(candidate.end_seconds || candidateStart);
      return candidateStart <= end + 0.75 && candidateEnd >= start - 0.75;
    });
  }

  async function syncStoredSentenceEntries(videoId, entries, transcriptSource) {
    if (!entries.length) return;
    const byId = new Map(entries.map((entry) => [entry.id, entry]));
    const changedEntries = await updateVideoState(videoId, (state) => {
      state.allSentenceEntries = entries;
      state.transcriptSource = transcriptSource;
      const shown = state.shownSentenceEntries || [];
      const changed = [];
      state.shownSentenceEntries = shown
        .map((entry) => {
          const replacement = replacementSentenceEntry(entry, byId, entries);
          if (!replacement) return entry;
          const next = {
            ...entry,
            ...replacement,
            text: normalizeText(replacement.text),
            source_text: normalizeText(replacement.source_text || replacement.text),
          };
          if (normalizeText(entry.text) !== next.text) {
            next.translated_text = "";
            next.translation_status = "";
            next.translation_reason = "";
            const translation = sentenceTranslationResults.get(sentenceTranslationKey(next));
            if (translation) {
              next.translated_text = normalizeText(translation.translated_text);
              next.translation_status = translation.status || "translated";
              next.translation_reason = translation.reason || "";
            } else {
              if (inFlightTranslations.has(sentenceTranslationKey(next))) {
                next.translation_status = "pending";
              }
              changed.push(next);
            }
          }
          return next;
        })
        .sort((left, right) => left.start_seconds - right.start_seconds);
      return changed;
    });
    for (const entry of changedEntries || []) {
      requestSentenceTranslation(videoId, entry);
    }
  }

  function restoreSentenceEntries(videoId) {
    if (sentenceEntries.length || !videoId || !ownsVideo(videoId) || contextInvalidated) return;
    try {
      chrome.storage.local.get(BY_VIDEO_KEY, (saved) => {
        const state = saved[BY_VIDEO_KEY]?.[videoId] || {};
        const entries = state.allSentenceEntries || [];
        if (entries.length && !sentenceEntries.length) {
          appendSentenceEntries(entries);
          return;
        }
        fetchPreparedTranscript(videoId, state.jobId || analysisOwner?.jobId);
      });
    } catch (error) {
      stopIfContextInvalidated(error);
    }
  }

  function fetchPreparedTranscript(videoId, jobId) {
    if (!authToken || !jobId || preparedTranscriptFetch || contextInvalidated) return;
    if (Date.now() - lastPreparedTranscriptFetchAt < PREPARED_TRANSCRIPT_FETCH_INTERVAL_MS) return;
    lastPreparedTranscriptFetchAt = Date.now();
    preparedTranscriptFetch = (async () => {
      try {
        if (!await storedOwnerMatches(videoId)) return;
        const job = await fetchJson(`/api/preparations/${jobId}?include_transcript=true&include_sentence_entries=true`);
        await rememberPreparationJob(videoId, job);
        const entries = normalizedSentenceEntries(job.sentence_entries || []);
        if (entries.length) appendSentenceEntries(entries);
        appendTranscriptSegments(job.segments || []);
        appendBubbles(job.bubbles || []);
        await updateVideoState(videoId, (state) => {
          state.transcriptSource = job.transcript_source;
          state.status = job.status === "ready" ? "Ready." : stageText(job);
        });
        await syncStoredSentenceEntries(videoId, entries, job.transcript_source);
      } catch (error) {
        setSharedStatus(videoId, error.message);
      } finally {
        preparedTranscriptFetch = null;
      }
    })();
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

  function setSharedStatus(videoId, text) {
    updateVideoState(videoId, (state) => {
      state.status = text;
    });
  }

  function stageText(job) {
    const stages = {
      queued: "Queued...",
      fetching_captions: "Checking captions...",
      caption_available: "Captions ready. Checking quality...",
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

  function pageStillMatches(videoId, currentVideo, requestGeneration) {
    return getVideoId() === videoId && findVideo() === currentVideo && pageGeneration === requestGeneration;
  }

  async function pollPreparation(videoId, job, currentVideo, requestGeneration) {
    let lastUpdated = job.updated_at || "";
    let lastMove = Date.now();
    while (true) {
      if (!await storedOwnerMatches(videoId)) throw new Error("Analysis ownership moved to another tab.");
      const entries = normalizedSentenceEntries(job.sentence_entries || []);
      setSharedStatus(videoId, stageText(job));
      if (pageStillMatches(videoId, currentVideo, requestGeneration)) {
        appendTranscriptSegments(job.segments || []);
        appendSentenceEntries(entries);
      }
      if (entries.length) {
        await syncStoredSentenceEntries(videoId, entries, job.transcript_source);
      }
      if (job.status === "ready") return job;
      if (job.status === "failed") throw new Error(job.message || "Preparation failed.");
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (!await storedOwnerMatches(videoId)) throw new Error("Analysis ownership moved to another tab.");
      job = await fetchJson(`/api/preparations/${job.job_id}?include_transcript=true&include_sentence_entries=true`);
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

  function enterVideo(videoId, currentVideo, forceReset = false) {
    if (!ownsVideo(videoId)) return;
    if (activeVideoId === videoId && !forceReset) return;
    activeVideoId = videoId;
    pageGeneration += 1;
    shownKeys = new Set();
    bubbles = [];
    transcriptSegments = [];
    sentenceEntries = [];
    resetTranslationState();
    lastCaptionText = "";
    lastFallbackCaptionAt = 0;
    loggedFallbackSegments = new Set();
    lastVideoTime = currentVideo?.currentTime || 0;
    overlay.clear();
    resetTimelineDisplay(videoId);
  }

  async function startAnalysis({ tabId, videoId: requestedVideoId, learnerLevel, sessionToken, demoMode = false, forceRefresh = false }) {
    if (analysisRunning) return { status: "already-running" };
    analysisRunning = true;
    authToken = sessionToken || "";
    translationForceRefresh = Boolean(forceRefresh);

    const videoId = requestedVideoId || "";
    const currentPageVideoId = getVideoId();
    const currentVideo = findVideo();
    try {
      if (!videoId) throw new Error("Open a YouTube watch page first.");
      if (videoId !== currentPageVideoId) {
        return {
          status: "active-video-changed",
          error: "The active video changed before analysis started. Please click Analyze again.",
          videoId,
        };
      }
      if (!currentVideo) throw new Error("No YouTube video element found.");
      if (!authToken) throw new Error("Pair the backend first.");
      await claimAnalysisOwner(tabId, videoId, forceRefresh);
      enterVideo(videoId, currentVideo, true);
      await resetTimelineDisplay(videoId, forceRefresh ? "Re-analyzing..." : "Analyzing...");
      const requestGeneration = pageGeneration;

      if (!await storedOwnerMatches(videoId)) throw new Error("Analysis ownership moved to another tab.");
      let job = await startPreparation(videoId, learnerLevel, demoMode, forceRefresh);
      await rememberPreparationJob(videoId, job);
      job = await pollPreparation(videoId, job, currentVideo, requestGeneration);
      await rememberPreparationJob(videoId, job);
      if (!await storedOwnerMatches(videoId)) throw new Error("Analysis ownership moved to another tab.");
      job = await fetchJson(`/api/preparations/${job.job_id}?include_transcript=true&include_sentence_entries=true`);
      await rememberPreparationJob(videoId, job);
      const finalSentenceEntries = normalizedSentenceEntries(job.sentence_entries || []);
      await updateVideoState(videoId, (state) => {
        state.jobId = job.job_id;
        state.status = "Ready.";
        state.captionLog = state.captionLog || [];
      });
      await syncStoredSentenceEntries(videoId, finalSentenceEntries, job.transcript_source);
      const response = {
        videoId,
        count: (job.bubbles || []).length,
        segmentCount: (job.segments || []).length,
        sentenceCount: finalSentenceEntries.length,
        transcriptSource: job.transcript_source,
        jobId: job.job_id,
        analysisId: job.analysis_id,
      };
      if (!pageStillMatches(videoId, currentVideo, requestGeneration)) {
        return { ...response, status: "analysis-finished-background" };
      }

      transcriptSegments = [];
      sentenceEntries = [];
      bubbles = [];
      shownKeys = new Set();
      loggedFallbackSegments = new Set();
      lastFallbackCaptionAt = 0;
      overlay.clear();
      appendTranscriptSegments(job.segments || []);
      appendSentenceEntries(finalSentenceEntries);
      appendBubbles(job.bubbles || []);

      return response;
    } finally {
      analysisRunning = false;
    }
  }

  function tick() {
    const currentVideo = findVideo();
    const videoId = getVideoId();

    if (!currentVideo || !videoId) return;
    if (!ownsVideo(videoId)) {
      restoreAnalysisOwner();
      if (analysisOwner) overlay.clear();
      return;
    }
    if (trackedVideo !== currentVideo) {
      if (trackedVideo) trackedVideo.removeEventListener("seeking", overlay.clear);
      trackedVideo = currentVideo;
      trackedVideo.addEventListener("seeking", overlay.clear);
    }

    if (activeVideoId !== videoId) {
      enterVideo(videoId, currentVideo);
    }
    restoreSentenceEntries(videoId);
    if (Math.abs(currentVideo.currentTime - lastVideoTime) > 2.5) {
      overlay.clear();
    }
    lastVideoTime = currentVideo.currentTime;

    const activeSentence = sentenceEntries.find((entry) => {
      return currentVideo.currentTime >= entry.start_seconds && currentVideo.currentTime <= entry.end_seconds;
    });
    if (activeSentence) {
      showSentenceEntry(videoId, activeSentence).then(() => requestSentenceTranslation(videoId, activeSentence));
    }
    queueSentenceTranslationLookahead(videoId, currentVideo.currentTime);

    const visibleCaptionText = readCaptionText();
    const activeSegment = transcriptSegments.find((segment) => {
      return currentVideo.currentTime >= segment.start_seconds && currentVideo.currentTime <= segment.end_seconds;
    });
    if (!sentenceEntries.length) {
      appendCaptionLog(videoId, visibleCaptionText || activeSegment?.text, currentVideo.currentTime, activeSegment, !visibleCaptionText);
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

  function handleStorageChanged(changes, areaName) {
    if (areaName !== "local" || !changes[OWNER_KEY] || !analysisOwner) return;
    if (sameOwner(changes[OWNER_KEY].newValue, analysisOwner)) return;
    analysisOwner = null;
    analysisRunning = false;
    resetTranslationState();
    clearSessionOwner();
    overlay.clear();
  }

  chrome.runtime.onMessage.addListener(handleMessage);
  chrome.storage.onChanged.addListener(handleStorageChanged);
  tickTimer = setInterval(tick, 500);
  globalThis.__contextbubbleCleanup = () => {
    clearInterval(tickTimer);
    if (trackedVideo) trackedVideo.removeEventListener("seeking", overlay.clear);
    overlay.clear();
    chrome.runtime.onMessage.removeListener(handleMessage);
    chrome.storage.onChanged.removeListener(handleStorageChanged);
  };
})();
