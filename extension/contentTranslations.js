(function () {
  function translationDone(result) {
    return ["translated", "failed", "skipped"].includes(result?.status);
  }

  globalThis.contextbubbleTranslations = {
    translationDone,
  };
})();
