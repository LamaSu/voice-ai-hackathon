"""SpeechBrain ECAPA speaker embeddings (CPU).

Slimmed from WhoSpeaksLive's SpeechBrainProvider (src/embeddings/embedding_providers.py)
without its environment side effects (HF offline mode, cache dirs, thread limits).
trim_silence / pad_audio are from WhoSpeaksLive src/common/audio_utils.py.
"""

from __future__ import annotations

import threading

import numpy as np

from app.config import BACKEND_DIR
from app.perception.whospeaks.speaker_embedding_cluster import normalize_vector

SAMPLE_RATE = 16000
MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"


def trim_silence(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(audio) < int(sample_rate * 0.25):
        return audio
    frame = max(1, int(sample_rate * 0.03))
    hop = max(1, int(sample_rate * 0.01))
    if len(audio) < frame:
        return audio
    n = (len(audio) - frame) // hop + 1
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt(np.mean(audio[idx] ** 2, axis=1) + 1e-12)
    peak = float(rms.max())
    threshold = max(peak * 0.08, 0.003)
    active = np.nonzero(rms >= threshold)[0]
    if active.size == 0:
        return audio
    pad = int(sample_rate * 0.10)
    start = max(0, int(active[0]) * hop - pad)
    end = min(len(audio), int(active[-1]) * hop + frame + pad)
    return audio[start:end]


def pad_audio(audio: np.ndarray, minimum_seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    minimum_samples = int(max(0.0, minimum_seconds) * sample_rate)
    if len(audio) >= minimum_samples:
        return audio
    return np.pad(audio, (0, minimum_samples - len(audio))).astype(np.float32)


class EcapaEmbedder:
    def __init__(self, device: str = "cpu"):
        import torch
        from speechbrain.inference.speaker import EncoderClassifier

        torch.set_num_threads(2)
        self._torch = torch
        self._model = EncoderClassifier.from_hparams(
            source=MODEL_ID,
            savedir=str(BACKEND_DIR / "pretrained_models" / "spkrec-ecapa-voxceleb"),
            run_opts={"device": device},
        )
        self._lock = threading.Lock()

    def embed(self, audio: np.ndarray) -> np.ndarray:
        """float32 mono 16 kHz -> L2-normalized 192-dim vector."""
        audio = pad_audio(trim_silence(audio), 0.5)
        wav = self._torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).unsqueeze(0)
        with self._lock, self._torch.inference_mode():
            emb = self._model.encode_batch(wav, normalize=False)
        return normalize_vector(emb.squeeze().cpu().numpy())
