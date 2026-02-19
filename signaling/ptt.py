import io
import wave
import asyncio

from speech.stt import transcribe as stt_transcribe

MAX_AUDIO_BUFFER_BYTES = 18 * 1024 * 1024
PTT_MAX_RECORDING_DURATION = 3 * 60


class PTTMixin:

    def _start_recording(self) -> None:
        self._ptt_active = True
        self._audio_frames.clear()
        self._audio_buffer_size = 0
        self._ptt_recording_start = asyncio.get_event_loop().time()
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
            self._ptt_timeout_timer = None

    def _stop_recording(self) -> None:
        self._ptt_active = False
        if not self._audio_frames:
            return
        wav_bytes = self._frames_to_wav()
        self._audio_frames.clear()
        self._audio_buffer_size = 0
        asyncio.ensure_future(self._process_stt(wav_bytes))

    async def _process_stt(self, wav_bytes: bytes) -> None:
        # WAV 헤더(44 bytes) 제외한 실제 PCM 데이터 길이로 최소 녹음 시간 확인
        # sample_rate * channels * bit_depth(2 bytes) * 최소 0.15초
        min_bytes = int(self._audio_sample_rate * self._audio_channels * 2 * 0.15)
        pcm_size = len(wav_bytes) - 44  # WAV 헤더 크기
        if pcm_size < min_bytes:
            self.send_dc({"type": "PTT_TOO_SHORT"})
            return

        try:
            text = await stt_transcribe(wav_bytes)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[PTT] STT failed: {e}")
            self.send_dc({"type": "PTT_TOO_SHORT"})
            return

        self.send_dc({"type": "USER_STT", "text": text})
        if self._interview and not self._interview.finished:
            await self._handle_interview_answer(text)


    def _frames_to_wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(self._audio_channels)
            wf.setsampwidth(2)
            wf.setframerate(self._audio_sample_rate)
            for raw in self._audio_frames:
                wf.writeframes(raw)
        wav_bytes = buf.getvalue()
        buf.close()
        return wav_bytes
