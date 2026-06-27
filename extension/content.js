(function () {
  const API_BASE = "http://127.0.0.1:8000";

  let video;
  let bubbles = [];
  let shownKeys = new Set();
  let activeVideoId;
  let activeBubble;
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
  }

  async function startAnalysis() {
    const videoId = getVideoId();
    if (!videoId) throw new Error("Open a YouTube watch page first.");

    const startResponse = await fetch(`${API_BASE}/api/analyze`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video_id: videoId }),
    });
    if (!startResponse.ok) throw new Error("Backend did not start analysis.");

    const started = await startResponse.json();
    const resultResponse = await fetch(`${API_BASE}/api/analysis/${started.analysis_id}`);
    if (!resultResponse.ok) throw new Error("Backend did not return analysis.");

    const result = await resultResponse.json();
    bubbles = result.bubbles || [];
    shownKeys = new Set();
    removeBubble();
    return { videoId, count: bubbles.length };
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
        <button type="button" data-action="dismiss">Dismiss</button>
      </div>
    `;

    root.querySelector(".contextbubble-title").textContent = bubble.concept;
    renderText(root);
    root.addEventListener("click", handleBubbleClick);
    document.body.appendChild(root);
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
    if (action === "dismiss") removeBubble();
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
      expanded = false;
      removeBubble();
    }

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
    startAnalysis().then(sendResponse).catch((error) => {
      sendResponse({ error: error.message });
    });
    return true;
  });

  setInterval(tick, 500);
})();
