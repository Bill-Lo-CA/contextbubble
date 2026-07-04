(function () {
  const SAFE_SLOTS = ["top-right", "top-left", "middle-right", "middle-left", "bottom-right", "bottom-left"];
  const TOP_SLOTS = ["top-right", "top-left", "middle-right", "middle-left"];

  function create({ findPlayer, captionsOrControlsVisible }) {
    let visibleBubbles = [];

    function clear() {
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

    function remove(root) {
      const item = visibleBubbles.find((entry) => entry.root === root);
      if (item) clearTimeout(item.timer);
      root.remove();
      visibleBubbles = visibleBubbles.filter((entry) => entry.root !== root);
    }

    function scheduleRemoval(item) {
      clearTimeout(item.timer);
      if (item.expanded) return;
      item.startedAt = Date.now();
      item.timer = setTimeout(() => remove(item.root), item.remainingMs);
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
      if (action === "dismiss" || action === "known") remove(item.root);
      if (action === "expand") {
        item.expanded = !item.expanded;
        item.remainingMs = 8000;
        renderText(item);
        scheduleRemoval(item);
      }
    }

    function show(bubble) {
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

    return { clear, show };
  }

  globalThis.contextbubbleOverlay = { create };
})();
