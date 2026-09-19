/**
 * Offline HUD check: replay a metrics trace with no bot, no mic, no credits.
 *
 * Open the UI with `?replay=1` to watch the HUD render a recorded session.
 * This is how lane D verifies the HUD before the pipeline is wired, and how
 * we sanity-check it backstage without burning Gradium credits.
 *
 * Traces are Contract 4 payloads, the same shape `metrics.jsonl` records.
 */

// Second half of the trace carries a filler, so replay exercises the split
// readout as well as the plain one.
const TRACE = [
  { end_of_speech_to_first_audio_ms: 2180, stages: { "endpointing wait": 420, transcription: 260, "llm inference": 1180, "speech synthesis": 320 } },
  { end_of_speech_to_first_audio_ms: 640, stages: { "endpointing wait": 200, transcription: 120, "llm inference": 210, "speech synthesis": 110 } },
  { end_of_speech_to_first_audio_ms: 486, stages: { "endpointing wait": 180, transcription: 96, "llm inference": 132, "speech synthesis": 78 } },
  { end_of_speech_to_first_audio_ms: 442, stages: { "endpointing wait": 170, transcription: 88, "llm inference": 121, "speech synthesis": 63 } },
  { end_of_speech_to_first_audio_ms: 921, stages: { "endpointing wait": 190, transcription: 104, "llm inference": 540, "speech synthesis": 87 } },
  { end_of_speech_to_first_audio_ms: 458, stages: { "endpointing wait": 175, transcription: 90, "llm inference": 128, "speech synthesis": 65 } },
  { end_of_speech_to_first_audio_ms: 412, end_of_speech_to_first_content_ms: 2080, filler: "weighing", stages: { "endpointing wait": 180, transcription: 95, "llm inference": 520, "speech synthesis": 980 } },
  { end_of_speech_to_first_audio_ms: 395, end_of_speech_to_first_content_ms: 1960, filler: "thinking", stages: { "endpointing wait": 175, transcription: 92, "llm inference": 480, "speech synthesis": 940 } },
];

export function replayInto(hud, { intervalMs = 900, trace = TRACE } = {}) {
  let i = 0;
  const timer = setInterval(() => {
    if (i >= trace.length) {
      clearInterval(timer);
      return;
    }
    const sample = trace[i++];
    hud.record({ ...sample, detail: { labels: {}, replay: true } });
  }, intervalMs);
  return () => clearInterval(timer);
}
