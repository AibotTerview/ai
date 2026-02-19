import logging
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from signaling import stomp
from signaling.datachannel import DataChannelMixin
from signaling.ptt import PTTMixin, MAX_AUDIO_BUFFER_BYTES, PTT_MAX_RECORDING_DURATION
from signaling.interview_handler import InterviewMixin
from speech.audio_track import TTSAudioTrack

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

        self.closed = asyncio.Event()

    async def _on_track(self, track: MediaStreamTrack) -> None:
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
        if self.peer is None:
            return
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

    def cleanup(self) -> None:
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
