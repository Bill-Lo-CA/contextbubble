(function () {
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

  globalThis.contextbubblePreparation = {
    stageText,
  };
})();
