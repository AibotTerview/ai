import logging
import asyncio
import os
import tempfile
import time
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from aiortc.contrib.media import MediaRecorder
from signaling import stomp
from signaling.datachannel import DataChannelMixin
from signaling.ptt import PTTMixin, MAX_AUDIO_BUFFER_BYTES, PTT_MAX_RECORDING_DURATION
from signaling.interview_handler import InterviewMixin
from speech.audio_track import TTSAudioTrack
from storage.s3 import upload_file
from interview.services.result import save_interview_result

logger = logging.getLogger(__name__)

class WebRTCSession(DataChannelMixin, PTTMixin, InterviewMixin):
    def __init__(self, room_id: str, stomp_ws) -> None:
        self.room_id = room_id
        self.stomp_ws = stomp_ws
        self.peer = RTCPeerConnection()
        self.peer.on("track", self._on_track)
        self.peer.on("connectionstatechange", self._on_connection_state_change)
        self.peer.on("datachannel", self._on_datachannel)

        self._dc = None

        self._ptt_active = False
        self._audio_frames: list[bytes] = []
        self._audio_buffer_size = 0
        self._audio_sample_rate = 48000
        self._audio_channels = 1

        self._interview = None
        self._tts_track = TTSAudioTrack()
        self._gender = "male"

        self._remote_desc_set = False
        self._pending_remote_ice: list[dict] = []

        self._interview_timer: asyncio.TimerHandle | None = None
        self._ptt_timeout_timer: asyncio.TimerHandle | None = None
        self._ptt_recording_start: float = 0.0

        self._interview_start_time: float | None = None
        self._video_recorder: MediaRecorder | None = None
        self._recording_path: str | None = None
        self._finalize_done = False

        self.closed = asyncio.Event()

    async def _on_track(self, track: MediaStreamTrack) -> None:
        if track.kind == "video":
            if self._video_recorder is not None:
                return
            fd, path = tempfile.mkstemp(suffix=".mp4", prefix=f"recording_{self.room_id}_")
            os.close(fd)
            self._recording_path = path
            self._video_recorder = MediaRecorder(path)
            self._video_recorder.addTrack(track)
            await self._video_recorder.start()
            logger.info("[WebRTC] 비디오 녹화 시작: %s", path)
            return
        if track.kind != "audio":
            return
        while True:
            try:
                frame = await track.recv()
            except Exception:
                break
            self._audio_sample_rate = frame.sample_rate
            self._audio_channels = len(frame.layout.channels)
            if self._ptt_active:
                elapsed = asyncio.get_event_loop().time() - self._ptt_recording_start
                if elapsed >= PTT_MAX_RECORDING_DURATION:
                    self.send_dc({"type": "PTT_TIMEOUT"})
                    self._stop_recording()
                    continue
                raw = frame.to_ndarray().tobytes()
                if self._audio_buffer_size + len(raw) <= MAX_AUDIO_BUFFER_BYTES:
                    self._audio_frames.append(raw)
                    self._audio_buffer_size += len(raw)

    def _on_connection_state_change(self) -> None:
        state = self.peer.connectionState
        if state == "connected":
            asyncio.ensure_future(self.stomp_ws.close())
        elif state in ("failed", "closed"):
            from signaling.session import remove_session
            remove_session(self.room_id)

    async def handle_offer(self, payload: dict) -> None:
        await self.peer.setRemoteDescription(RTCSessionDescription(sdp=payload["sdp"], type=payload["type"]))

        self._remote_desc_set = True
        for ice_payload in self._pending_remote_ice:
            candidate = _parse_candidate(ice_payload["candidate"])
            candidate.sdpMid = ice_payload.get("sdpMid")
            candidate.sdpMLineIndex = ice_payload.get("sdpMLineIndex")
            await self.peer.addIceCandidate(candidate)
        self._pending_remote_ice.clear()

        self.peer.addTrack(self._tts_track)
        answer = await self.peer.createAnswer()
        await self.peer.setLocalDescription(answer)

        for _ in range(200):
            if self.peer.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.05)

        await stomp.send(self.stomp_ws, "/app/signal/webrtc/answer", {
            "type": "WEBRTC_ANSWER",
            "roomId": self.room_id,
            "payload": {"sdp": self.peer.localDescription.sdp, "type": self.peer.localDescription.type},
        })

    async def handle_ice(self, payload: dict) -> None:
        if not self._remote_desc_set:
            self._pending_remote_ice.append(payload)
            return
        candidate = _parse_candidate(payload["candidate"])
        candidate.sdpMid = payload.get("sdpMid")
        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
        await self.peer.addIceCandidate(candidate)

    def _finalize_recording_and_save(self) -> None:
        """녹화 중지 → S3 업로드 → 로컬 삭제 → DB 저장. cleanup()에서 한 번만 호출."""
        if self._finalize_done:
            return
        self._finalize_done = True

        recorder = self._video_recorder
        path = self._recording_path
        self._video_recorder = None
        self._recording_path = None

        if recorder is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(recorder.stop())
                loop.close()
            except Exception as e:
                logger.exception("[WebRTC] 녹화 중지 실패: %s", e)

        duration = 0.0
        if self._interview_start_time is not None:
            duration = max(0.0, time.time() - self._interview_start_time)

        video_url = ""
        if path and os.path.isfile(path):
            key = f"interviews/{self.room_id}/recording_{int(time.time())}.mp4"
            video_url = upload_file(path, key) or ""
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("[WebRTC] 로컬 파일 삭제 실패: %s", e)
            if video_url:
                save_interview_result(self.room_id, video_url, "", duration)
                logger.info("[WebRTC] 인터뷰 결과 저장 완료: room_id=%s duration=%.1fs", self.room_id, duration)

    def cleanup(self) -> None:
        self._finalize_recording_and_save()

        if self._interview_timer:
            self._interview_timer.cancel()
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()

        self._ptt_active = False
        self._audio_frames.clear()
        self._interview = None

        if self._tts_track:
            self._tts_track.stop()
            self._tts_track = None

        if self._dc:
            self._dc.close()
            self._dc = None

        if self.peer:
            asyncio.ensure_future(self.peer.close())
            self.peer = None

        self.closed.set()


def _parse_candidate(candidate_str: str) -> RTCIceCandidate:
    parts = candidate_str.replace("candidate:", "").split()
    candidate = RTCIceCandidate(
        component=int(parts[1]),
        foundation=parts[0],
        ip=parts[4],
        port=int(parts[5]),
        priority=int(parts[3]),
        protocol=parts[2],
        type=parts[7],
    )
    if "raddr" in parts:
        candidate.relatedAddress = parts[parts.index("raddr") + 1]
    if "rport" in parts:
        candidate.relatedPort = int(parts[parts.index("rport") + 1])
    return candidate
