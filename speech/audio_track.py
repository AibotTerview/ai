import asyncio
import fractions
import time
from av import AudioFrame
from aiortc.mediastreams import MediaStreamTrack

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960
FRAME_DURATION = SAMPLES_PER_FRAME / SAMPLE_RATE
SILENCE = b"\x00" * (SAMPLES_PER_FRAME * 2)


class TTSAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._start_time: float | None = None
        self._frame_count = 0
        self._done_event: asyncio.Event | None = None

    async def recv(self) -> AudioFrame:
        if self._start_time is None:
            self._start_time = time.time()

        target_time = self._start_time + self._frame_count * FRAME_DURATION
        wait = target_time - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

        pcm_data = self._dequeue_frame()

        frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._frame_count * SAMPLES_PER_FRAME
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        frame.planes[0].update(pcm_data)

        self._frame_count += 1
        return frame

    def _dequeue_frame(self) -> bytes:
        if self._queue.empty():
            return SILENCE
        data = self._queue.get_nowait()
        if data is None:
            if self._done_event:
                self._done_event.set()
            return SILENCE
        return data

    async def play(self, pcm_bytes: bytes) -> None:
        self._done_event = asyncio.Event()

        frame_bytes = SAMPLES_PER_FRAME * 2
        for i in range(0, len(pcm_bytes), frame_bytes):
            chunk = pcm_bytes[i : i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            self._queue.put_nowait(chunk)

        self._queue.put_nowait(None)
        await self._done_event.wait()
        self._done_event = None
