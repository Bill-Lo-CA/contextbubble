(function () {
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

  function create({ stopIfContextInvalidated }) {
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

    return { readLocal, writeLocal };
  }

  globalThis.contextbubbleStorage = {
    create,
    formatTime,
    normalizeText,
  };
})();
