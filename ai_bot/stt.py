import logging
import os
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)


def transcribe_audio(audio_path: str) -> str | None:
    """
    OpenAI Whisper API를 사용하여 오디오 파일을 텍스트로 변환합니다.

    Args:
        audio_path: 변환할 오디오 파일 경로 (.wav, .mp3 등)

    Returns:
        변환된 텍스트 또는 실패 시 None
    """
    # OpenAI API 키가 없으면 스킵
    if not settings.OPENAI_API_KEY:
        logger.warning("[STT] OpenAI API key not found. Skipping transcription.")
        return None

    # 파일 존재 여부 확인
    if not os.path.exists(audio_path):
        logger.error(f"[STT] Audio file not found: {audio_path}")
        return None

    try:
        logger.info(f"[STT] Starting transcription for: {audio_path}")

        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ko"  # 한국어 지정 (자동 감지도 가능)
            )

        text = transcript.text
        logger.info(f"[STT] Transcription successful. Length: {len(text)} chars")
        logger.debug(f"[STT] Transcribed text: {text[:100]}...")  # 처음 100자만 로그

        return text

    except Exception as e:
        logger.error(f"[STT] Transcription failed: {e}")
        return None
