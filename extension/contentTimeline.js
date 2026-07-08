(function () {
  function entryAt(entries, currentTime) {
    return entries.find((entry) => {
      return currentTime >= entry.start_seconds && currentTime <= entry.end_seconds;
    });
  }

  function bubbleKey(videoId, bubble) {
    return `${videoId}:${bubble.concept}:${bubble.start_seconds}`;
  }

  function markSkippedBubbles(bubbles, shownKeys, videoId, currentTime) {
    for (const bubble of bubbles) {
      const key = bubbleKey(videoId, bubble);
      if (!shownKeys.has(key) && currentTime > bubble.start_seconds + 1.5) {
        shownKeys.add(key);
      }
    }
  }

  function dueBubble(bubbles, shownKeys, videoId, currentTime) {
    return bubbles.find((bubble) => {
      const key = bubbleKey(videoId, bubble);
      return !shownKeys.has(key)
        && currentTime >= bubble.start_seconds - 0.3
        && currentTime <= bubble.start_seconds + 1.5;
    });
  }

  globalThis.contextbubbleTimeline = {
    bubbleKey,
    dueBubble,
    entryAt,
    markSkippedBubbles,
  };
})();
