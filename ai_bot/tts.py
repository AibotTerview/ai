import io
import json
import wave
import base64
import logging
import asyncio
import urllib.request
from functools import partial
from django.conf import settings

logger = logging.getLogger(__name__)

# ── 음성 설정 ─────────────────────────────────────────

VOICE_CONFIGS = {
    "male": {
        "name": "ko-KR-Wavenet-C",
        "ssmlGender": "MALE",
    },
    "female": {
        "name": "ko-KR-Wavenet-A",
        "ssmlGender": "FEMALE",
    },
}

TTS_SAMPLE_RATE = 48000


# ── API 호출 ──────────────────────────────────────────

def _synthesize_sync(text: str, gender: str = "male") -> bytes:
    """Google TTS REST API 호출 → raw PCM bytes 반환"""
    voice_config = VOICE_CONFIGS.get(gender, VOICE_CONFIGS["male"])

    url = (
        "https://texttospeech.googleapis.com/v1/text:synthesize"
        f"?key={settings.GOOGLE_TTS_API_KEY}"
    )
    body = json.dumps({
        "input": {"text": text},
        "voice": {
            "languageCode": "ko-KR",
            "name": voice_config["name"],
            "ssmlGender": voice_config["ssmlGender"],
        },
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": TTS_SAMPLE_RATE,
            "speakingRate": 1.0,
            "pitch": 0,
        },
    }).encode()

    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    wav_data = base64.b64decode(result["audioContent"])

    # WAV 헤더에서 raw PCM 추출
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        pcm_bytes = wf.readframes(wf.getnframes())
    buf.close()

    logger.info(f"[TTS] 변환 완료: {len(text)}자 → {len(pcm_bytes)} bytes PCM")
    return pcm_bytes


async def synthesize(text: str, gender: str = "male") -> bytes:
    """비동기 TTS 호출"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(_synthesize_sync, text, gender)
    )
