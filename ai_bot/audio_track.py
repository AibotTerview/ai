import asyncio
import fractions
import time
import logging
from av import AudioFrame
from aiortc.mediastreams import MediaStreamTrack

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960  # 20ms at 48kHz
FRAME_DURATION = SAMPLES_PER_FRAME / SAMPLE_RATE  # 0.02s
SILENCE = b"\x00" * (SAMPLES_PER_FRAME * 2)  # 16bit mono


class TTSAudioTrack(MediaStreamTrack):
    """TTS 오디오를 WebRTC로 스트리밍하는 커스텀 오디오 트랙"""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._start_time: float | None = None
        self._frame_count = 0

    async def recv(self) -> AudioFrame:
        # 첫 프레임 시 타이밍 기준점 설정
        if self._start_time is None:
            self._start_time = time.time()

        # 프레임 타이밍 맞추기 (20ms 간격)
        target_time = self._start_time + self._frame_count * FRAME_DURATION
        wait = target_time - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

        # 큐에서 프레임 데이터 가져오기 (없으면 무음)
        pcm_data = self._dequeue_frame()

        # AudioFrame 생성
        frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._frame_count * SAMPLES_PER_FRAME
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        frame.planes[0].update(pcm_data)

        self._frame_count += 1
        return frame

    def _dequeue_frame(self) -> bytes:
        """큐에서 다음 프레임 가져오기, 없으면 무음 반환"""
        if self._queue.empty():
            return SILENCE

        data = self._queue.get_nowait()

        if data is None:  # 재생 완료 센티넬
            if self._done_event:
                self._done_event.set()
            return SILENCE

        return data
