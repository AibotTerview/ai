import av
import io
import fractions
import logging
from av import AudioFrame, VideoFrame
from storage.s3 import S3MultipartUpload, S3_INTERVIEW_PREFIX

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960


class InterviewRecorder:
    def __init__(self, room_id: str):
        key = f"{S3_INTERVIEW_PREFIX}/{room_id}/{room_id}.mp4"
        self._uploader = S3MultipartUpload(key)
        self._key = key
        self._buf = io.BytesIO()
        self._buf_flushed = 0  # S3에 전송된 바이트 수
        self._container = None
        self._v_stream = None
        self._a_stream = None
        self._running = False
        self._audio_pts = 0
        self._video_pts = 0

    # 고정 녹화 해상도 (프레임은 이 크기로 스케일링됨)
    VIDEO_WIDTH = 1280
    VIDEO_HEIGHT = 720

    def start(self) -> None:
        self._uploader.start()
        self._buf = io.BytesIO()
        self._buf_flushed = 0
        self._container = av.open(
            self._buf,
            mode="w",
            format="mp4",
            options={"movflags": "frag_keyframe+empty_moov+default_base_moof"},
        )
        # 비디오 스트림: width/height를 미리 고정 (avformat_write_header 전에 설정 필수)
        self._v_stream = self._container.add_stream("h264", rate=30)
        self._v_stream.width = self.VIDEO_WIDTH
        self._v_stream.height = self.VIDEO_HEIGHT
        self._v_stream.pix_fmt = "yuv420p"
        self._v_stream.options = {"preset": "ultrafast", "tune": "zerolatency"}

        self._a_stream = self._container.add_stream("aac", rate=SAMPLE_RATE)
        self._a_stream.layout = "mono"
        self._running = True
        self._audio_pts = 0
        self._video_pts = 0
        logger.info("[Recorder] 녹화 시작: %s", self._key)

    def push_video(self, frame: VideoFrame) -> None:
        if not self._running:
            return
        try:
            frame = frame.reformat(
                width=self.VIDEO_WIDTH,
                height=self.VIDEO_HEIGHT,
                format="yuv420p",
            )
        except Exception as e:
            logger.debug("[Recorder] 비디오 프레임 변환 실패 (스킵): %s", e)
            return
        if frame.width == 0 or frame.height == 0:
            return
        # aiortc RTP 클록은 랜덤 오프셋에서 시작하므로 단조증가 PTS로 덮어씀
        frame.pts = self._video_pts
        frame.time_base = fractions.Fraction(1, 30)
        self._video_pts += 1
        try:
            for packet in self._v_stream.encode(frame):
                self._container.mux(packet)
                self._flush_to_s3()
        except Exception as e:
            logger.warning("[Recorder] 비디오 인코딩/mux 실패 (스킵): %s", e)

    def push_audio_pcm(self, pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> None:
        """TTS PCM bytes 또는 마이크 raw PCM bytes 입력.
        sample_rate가 SAMPLE_RATE(48000)와 다를 경우 av.AudioResampler로 리샘플링.
        """
        if not self._running:
            return
        # 샘플레이트가 다른 경우 48000Hz로 리샘플링
        if sample_rate != SAMPLE_RATE:
            try:
                resampler = av.AudioResampler(
                    format="s16", layout="mono", rate=SAMPLE_RATE
                )
                tmp = AudioFrame(format="s16", layout="mono", samples=len(pcm_bytes) // 2)
                tmp.sample_rate = sample_rate
                tmp.pts = 0
                tmp.time_base = fractions.Fraction(1, sample_rate)
                tmp.planes[0].update(pcm_bytes)
                resampled = resampler.resample(tmp)
                if resampled:
                    pcm_bytes = resampled[0].planes[0].to_bytes()
                else:
                    return
            except Exception as e:
                logger.debug("[Recorder] 오디오 리샘플링 실패 (스킵): %s", e)
                return
        frame_bytes = SAMPLES_PER_FRAME * 2
        for i in range(0, len(pcm_bytes), frame_bytes):
            chunk = pcm_bytes[i : i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
            frame.sample_rate = SAMPLE_RATE
            frame.pts = self._audio_pts
            frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
            frame.planes[0].update(chunk)
            self._audio_pts += SAMPLES_PER_FRAME
            for packet in self._a_stream.encode(frame):
                self._container.mux(packet)
                self._flush_to_s3()

    def _flush_to_s3(self) -> None:
        # BytesIO를 truncate하지 않고 새로 쓰인 부분만 S3로 전송
        # truncate하면 FFmpeg avio의 내부 위치와 BytesIO 위치가 어긋나 mux 실패
        end = self._buf.seek(0, 2)  # seek to end, returns total size
        if end > self._buf_flushed:
            self._buf.seek(self._buf_flushed)
            new_data = self._buf.read(end - self._buf_flushed)
            self._uploader.write(new_data)
            self._buf_flushed = end

    def stop(self) -> str | None:
        """녹화 종료. 완성된 S3 URL 반환."""
        if not self._running:
            return None
        self._running = False
        try:
            for packet in self._v_stream.encode():
                self._container.mux(packet)
            for packet in self._a_stream.encode():
                self._container.mux(packet)
            self._container.close()
            self._flush_to_s3()
            url = self._uploader.complete()
            logger.info("[Recorder] 녹화 완료: %s", url)
            return url
        except Exception as e:
            logger.error("[Recorder] 녹화 종료 실패: %s", e)
            self._uploader.abort()
            return None
