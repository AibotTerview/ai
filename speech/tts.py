import io
import json
import wave
import base64
import asyncio
import urllib.request
from functools import partial
from django.conf import settings

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

def _synthesize_sync(text: str, gender: str) -> bytes:
    voice_config = VOICE_CONFIGS.get(gender)

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
            "sampleRateHertz": 48000,
            "speakingRate": 1.0,
            "pitch": 0,
        },
    }).encode()

    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30)

    try:
        result = json.loads(resp.read())
    finally:
        resp.close()

    wav_data = base64.b64decode(result["audioContent"])

    buf = io.BytesIO(wav_data)
    wf = wave.open(buf, "rb")
    try:
        pcm_bytes = wf.readframes(wf.getnframes())
    finally:
        wf.close()
    buf.close()

    return pcm_bytes

async def synthesize(text: str, gender: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(_synthesize_sync, text, gender)
    )
