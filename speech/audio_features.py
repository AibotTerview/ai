
import io
import wave
import numpy as np

CHUNK_SAMPLES = 960
SILENCE_THRESHOLD_RATIO = 0.02


def extract_features(wav_bytes: bytes) -> dict | None:
    buf = io.BytesIO(wav_bytes)
    try:
        wf = wave.open(buf, "rb")
    except Exception:
        return None
    try:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        if nframes <= 0 or framerate <= 0:
            return None
        duration_sec = nframes / framerate
        raw = wf.readframes(nframes)
    finally:
        wf.close()
    buf.close()

    if sampwidth != 2:  # 16-bit expected
        return {"duration_sec": duration_sec, "silence_ratio": 0.0}

    samples = np.frombuffer(raw, dtype=np.int16)
    if nchannels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1).astype(np.int16)
    num_chunks = max(1, len(samples) // CHUNK_SAMPLES)
    rms_list = []
    for i in range(num_chunks):
        start = i * CHUNK_SAMPLES
        end = min(start + CHUNK_SAMPLES, len(samples))
        chunk = samples[start:end].astype(np.float64) / 32768.0
        rms = np.sqrt(np.mean(chunk * chunk))
        rms_list.append(rms)
    rms_max = max(rms_list) if rms_list else 1.0
    threshold = max(0.005, SILENCE_THRESHOLD_RATIO * rms_max)
    silence_chunks = sum(1 for r in rms_list if r <= threshold)
    silence_ratio = silence_chunks / num_chunks if num_chunks else 0.0

    return {
        "duration_sec": round(duration_sec, 2),
        "silence_ratio": round(silence_ratio, 3),
    }
