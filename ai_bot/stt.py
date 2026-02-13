import io
import logging
import asyncio
from functools import partial
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _transcribe_sync(wav_bytes: bytes) -> str:
    """동기 Whisper API 호출 (스레드에서 실행)"""
    client = _get_client()
    buf = io.BytesIO(wav_bytes)
    buf.name = "audio.wav"

    response = client.audio.transcriptions.create(
        model="whisper-1",
        file=buf,
        language="ko",
    )
    buf.close()
    return response.text


MAX_RETRIES = 1


async def transcribe(wav_bytes: bytes) -> str:
    """비동기 Whisper STT — 재시도 1회 포함"""
    loop = asyncio.get_event_loop()
    last_error: Exception | None = None

    for attempt in range(1 + MAX_RETRIES):
        try:
            text = await loop.run_in_executor(
                None, partial(_transcribe_sync, wav_bytes)
            )
            logger.info(f"[STT] 결과: {text[:100]}...")
            return text
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                logger.warning(f"[STT] 실패 (시도 {attempt + 1}), 재시도: {e}")
                await asyncio.sleep(0.5)
            else:
                logger.error(f"[STT] 최종 실패: {e}")

    return f"[STT 오류] {last_error}"
