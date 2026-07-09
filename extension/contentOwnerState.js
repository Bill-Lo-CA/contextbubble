(function () {
  const OWNER_SESSION_KEY = "contextbubbleAnalysisOwner";

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

  globalThis.contextbubbleOwnerState = {
    clearSessionOwner,
    readSessionOwner,
    sameOwner,
    writeSessionOwner,
  };
})();
