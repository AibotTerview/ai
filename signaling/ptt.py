import io
import wave
import asyncio

from speech.stt import stt

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
        wav_bytes = self._frames_to_wav()
        self._audio_frames.clear()
        self._audio_buffer_size = 0
        asyncio.ensure_future(self._process_stt(wav_bytes))

    async def _process_stt(self, wav_bytes: bytes) -> None:
        text = await stt(wav_bytes)
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
