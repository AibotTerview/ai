import io
import wave
import logging
import asyncio

from ..stt import transcribe as stt_transcribe

logger = logging.getLogger(__name__)

# PTT 오디오 버퍼 최대 크기 (3분 @ 48kHz mono 16bit ≈ 17MB)
MAX_AUDIO_BUFFER_BYTES = 18 * 1024 * 1024

# PTT 최대 녹음 시간 (초)
PTT_MAX_RECORDING_DURATION = 3 * 60


class PTTMixin:
    """PTT(Push-to-Talk) 오디오 버퍼링 mixin — WebRTCSession에서 사용"""

    def _start_recording(self) -> None:
        logger.info("[PTT] 녹음 시작")
        self._ptt_active = True
        self._audio_frames.clear()
        self._audio_buffer_size = 0
        self._ptt_recording_start = asyncio.get_event_loop().time()
        # PTT 무응답 타이머 취소 (사용자가 응답함)
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
            self._ptt_timeout_timer = None

    def _stop_recording(self) -> None:
        logger.info(f"[PTT] 녹음 종료 — {len(self._audio_frames)}프레임, {self._audio_buffer_size}bytes")
        self._ptt_active = False

        if not self._audio_frames:
            logger.warning("[PTT] 오디오 프레임 없음, 무시")
            return

        wav_bytes = self._frames_to_wav()
        self._audio_frames.clear()
        self._audio_buffer_size = 0

        # STT 파이프라인 실행
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(self._process_stt(wav_bytes), loop=loop)

    async def _process_stt(self, wav_bytes: bytes) -> None:
        """WAV → Whisper STT → DataChannel로 결과 전송 → LLM 면접관"""
        text = await stt_transcribe(wav_bytes)
        self.send_dc({"type": "USER_STT", "text": text})
        logger.info(f"[STT→DC] USER_STT 전송: {text[:80]}...")

        # 면접 세션이 있으면 LLM으로 다음 질문 생성
        if self._interview and not self._interview.finished:
            await self._handle_interview_answer(text)

        # 외부 콜백이 있으면 호출
        if self.on_ptt_audio:
            await self.on_ptt_audio(text)

    def _frames_to_wav(self) -> bytes:
        """누적된 오디오 프레임을 WAV 바이트로 변환"""
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(self._audio_channels)
            wf.setsampwidth(2)  # 16bit
            wf.setframerate(self._audio_sample_rate)
            for raw in self._audio_frames:
                wf.writeframes(raw)
        wav_bytes = buf.getvalue()
        buf.close()
        logger.info(f"[PTT] WAV 변환 완료: {len(wav_bytes)} bytes")
        return wav_bytes
