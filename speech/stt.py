import io
import asyncio
from functools import partial
from openai import OpenAI
from django.conf import settings

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _transcribe_sync(wav_bytes: bytes) -> str:
    buf = io.BytesIO(wav_bytes)
    buf.name = "audio.wav"
    response = _get_client().audio.transcriptions.create(
        model="whisper-1",
        file=buf,
        language="ko",
    )
    buf.close()
    return response.text


async def transcribe(wav_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_transcribe_sync, wav_bytes))
