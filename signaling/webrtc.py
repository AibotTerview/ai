import gc
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
        self.peer.on("icecandidate", self._on_ice_candidate)
        self.peer.on("track", self._on_track)
        self.peer.on("connectionstatechange", self._on_connection_state_change)
        self.peer.on("datachannel", self._on_datachannel)

        # DataChannel
        self._dc = None

        # PTT 오디오 버퍼링
        self._ptt_active = False
        self._audio_frames: list[bytes] = []
        self._audio_buffer_size = 0
        self._audio_sample_rate = 48000
        self._audio_channels = 1

        # 콜백: PTT_END 시 WAV 데이터를 처리하는 함수 (외부에서 설정)
        self.on_ptt_audio = None  # async def callback(wav_bytes: bytes)

        # 면접 세션 (DataChannel 연결 시 초기화)
        self._interview = None

        # TTS 오디오 트랙 (offer 처리 시 peer에 추가)
        self._tts_track = TTSAudioTrack()
        self._gender = "male"

        # ICE candidate buffering (remote description 설정 전 도착한 후보 버퍼링)
        self._remote_desc_set = False
        self._pending_remote_ice: list[dict] = []

        # 세션 상태
        self._cleaned_up = False
        self._interview_timer: asyncio.TimerHandle | None = None
        self._ptt_timeout_timer: asyncio.TimerHandle | None = None
        self._ptt_recording_start: float = 0.0
        self.closed = asyncio.Event()

    # ── ICE ──────────────────────────────────────────

    def _on_ice_candidate(self, candidate) -> None:
        """ICE 후보 발생 시 STOMP로 전송"""
        if candidate:
            asyncio.ensure_future(self._send_ice_candidate(candidate))

    async def _send_ice_candidate(self, candidate) -> None:
        """ICE 후보를 STOMP로 전송"""
        try:
            await stomp.send(self.stomp_ws, "/app/signal/webrtc/ice", {
                "type": "ICE_CANDIDATE",
                "roomId": self.room_id,
                "payload": {
                    "candidate": f"candidate:{candidate.foundation} {candidate.component} {candidate.protocol} {candidate.priority} {candidate.host} {candidate.port} typ {candidate.type}",
                    "sdpMid": getattr(candidate, 'sdpMid', '0'),
                    "sdpMLineIndex": getattr(candidate, 'sdpMLineIndex', 0),
                },
            })
        except Exception as e:
            logger.error(f"[ICE 이벤트] 전송 실패: {e}")

    # ── Track 수신 (오디오/비디오) ───────────────────

    async def _on_track(self, track: MediaStreamTrack) -> None:
        """오디오 트랙 수신 및 PTT 버퍼링"""
        if track.kind != "audio":
            return

        while True:
            try:
                frame = await track.recv()
            except Exception:
                break

            self._audio_sample_rate = frame.sample_rate
            self._audio_channels = len(frame.layout.channels)

            # PTT 활성 시에만 버퍼에 저장
            if self._ptt_active:
                # 최대 녹음 시간 초과 시 자동 종료
                elapsed = asyncio.get_event_loop().time() - self._ptt_recording_start
                if elapsed >= PTT_MAX_RECORDING_DURATION:
                    self.send_dc({"type": "PTT_TIMEOUT"})
                    self._stop_recording()
                    continue

                raw = frame.to_ndarray().tobytes()
                if self._audio_buffer_size + len(raw) <= MAX_AUDIO_BUFFER_BYTES:
                    self._audio_frames.append(raw)
                    self._audio_buffer_size += len(raw)

    # ── 연결 상태 ────────────────────────────────────

    def _on_connection_state_change(self) -> None:
        """연결 상태 변경 처리"""
        state = self.peer.connectionState
        if state == "connected":
            asyncio.ensure_future(self.stomp_ws.close())
        elif state in ("failed", "closed"):
            from signaling.session import remove_session
            remove_session(self.room_id)

    # ── SDP 핸들링 ───────────────────────────────────

    async def handle_offer(self, payload: dict) -> None:
        """WebRTC Offer 처리 및 Answer 생성"""
        offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        await self.peer.setRemoteDescription(offer)

        # 버퍼링된 ICE 후보 처리
        self._remote_desc_set = True
        for ice_payload in self._pending_remote_ice:
            try:
                candidate = _parse_candidate(ice_payload["candidate"])
                candidate.sdpMid = ice_payload.get("sdpMid")
                candidate.sdpMLineIndex = ice_payload.get("sdpMLineIndex")
                await self.peer.addIceCandidate(candidate)
            except Exception as e:
                logger.error(f"[ICE] 버퍼링 후보 추가 실패: {e}")
        self._pending_remote_ice.clear()

        # TTS 오디오 트랙 추가
        self.peer.addTrack(self._tts_track)

        # Answer 생성 및 전송
        answer = await self.peer.createAnswer()
        await self.peer.setLocalDescription(answer)

        # ICE gathering 완료 대기 (최대 10초)
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
        """원격 ICE 후보 처리"""
        if not self._remote_desc_set:
            self._pending_remote_ice.append(payload)
            return

        try:
            candidate = _parse_candidate(payload["candidate"])
            candidate.sdpMid = payload.get("sdpMid")
            candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
            await self.peer.addIceCandidate(candidate)
        except Exception as e:
            logger.error(f"[ICE] 원격 후보 추가 실패: {e}")

    # ── 리소스 정리 ──────────────────────────────────

    def cleanup(self) -> None:
        """세션 리소스 정리"""
        if self._cleaned_up:
            return
        self._cleaned_up = True

        # 타이머 취소
        if self._interview_timer:
            self._interview_timer.cancel()
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()

        # 오디오 버퍼 정리
        self._ptt_active = False
        self._audio_frames.clear()
        self._audio_buffer_size = 0

        # 면접 세션 정리
        self._interview = None

        # TTS 트랙 정리
        if self._tts_track:
            self._tts_track.stop()
            self._tts_track = None

        # DataChannel 정리
        if self._dc:
            try:
                self._dc.close()
            except Exception:
                pass
            self._dc = None

        # PeerConnection 정리
        if self.peer:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.peer.close(), loop=loop)
                else:
                    loop.run_until_complete(self.peer.close())
            except Exception as e:
                logger.warning(f"[세션 정리] peer close 실패: {e}")
            self.peer = None

        gc.collect()
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
