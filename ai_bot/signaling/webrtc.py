import gc
import logging
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from websocket import WebSocket
from . import stomp
from .datachannel import DataChannelMixin
from .ptt import PTTMixin, MAX_AUDIO_BUFFER_BYTES, PTT_MAX_RECORDING_DURATION
from .interview_handler import InterviewMixin
from ..audio_track import TTSAudioTrack

logger = logging.getLogger(__name__)


class WebRTCSession(DataChannelMixin, PTTMixin, InterviewMixin):
    def __init__(self, room_id: str, stomp_ws: WebSocket) -> None:
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

    # ── ICE ──────────────────────────────────────────

    def _on_ice_candidate(self, candidate) -> None:
        logger.info(f"[ICE 이벤트] icecandidate 발생: {candidate}")
        if candidate:
            try:
                stomp.send(self.stomp_ws, "/app/signal/webrtc/ice", {
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

    async def _send_local_candidates(self) -> None:
        """ICE gathering 완료 후 로컬 후보를 프론트엔드로 STOMP 전송"""
        try:
            ice_connection = None
            mid = "0"

            # BUNDLE 정책: 첫 번째 transceiver의 ICE transport에서 추출
            for transceiver in getattr(self.peer, '_transceivers', []):
                dtls = getattr(transceiver, '_transport', None)
                if dtls is None:
                    continue
                ice = getattr(dtls, 'transport', None)
                if ice is None:
                    continue
                ice_connection = getattr(ice, '_connection', None)
                mid = getattr(transceiver, 'mid', '0') or '0'
                if ice_connection:
                    break

            # SCTP transport에서도 시도
            if not ice_connection:
                sctp = getattr(self.peer, '_sctpTransport', None)
                if sctp:
                    dtls = getattr(sctp, '_dtls_transport', None) or getattr(sctp, 'transport', None)
                    if dtls:
                        ice = getattr(dtls, 'transport', None)
                        if ice:
                            ice_connection = getattr(ice, '_connection', None)

            if not ice_connection:
                logger.warning("[ICE 전송] ICE connection을 찾을 수 없음")
                return

            candidates = getattr(ice_connection, 'local_candidates', [])
            logger.info(f"[ICE 전송] 로컬 후보 {len(candidates)}개 발견")

            for c in candidates:
                candidate_str = (
                    f"candidate:{c.foundation} {c.component} {c.protocol} "
                    f"{c.priority} {c.host} {c.port} typ {c.type}"
                )
                if getattr(c, 'related_address', None):
                    candidate_str += f" raddr {c.related_address} rport {c.related_port}"

                stomp.send(self.stomp_ws, "/app/signal/webrtc/ice", {
                    "type": "ICE_CANDIDATE",
                    "roomId": self.room_id,
                    "payload": {
                        "candidate": candidate_str,
                        "sdpMid": mid,
                        "sdpMLineIndex": 0,
                    },
                })
                logger.info(f"[ICE 전송] {candidate_str}")
        except Exception as e:
            logger.error(f"[ICE 전송 실패] {e}", exc_info=True)

    # ── Track 수신 (오디오/비디오) ───────────────────

    async def _on_track(self, track: MediaStreamTrack) -> None:
        logger.info(f"[TRACK 수신] kind={track.kind}, id={track.id}")

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
                # PTT 최대 녹음 시간 초과 시 자동 종료
                elapsed = asyncio.get_event_loop().time() - self._ptt_recording_start
                if elapsed >= PTT_MAX_RECORDING_DURATION:
                    logger.warning(f"[PTT] 최대 녹음 시간({PTT_MAX_RECORDING_DURATION}초) 초과, 자동 종료")
                    self.send_dc({"type": "PTT_TIMEOUT"})
                    self._stop_recording()
                    continue

                raw = frame.to_ndarray().tobytes()
                if self._audio_buffer_size + len(raw) <= MAX_AUDIO_BUFFER_BYTES:
                    self._audio_frames.append(raw)
                    self._audio_buffer_size += len(raw)
                else:
                    logger.warning("[PTT] 오디오 버퍼 최대 크기 초과, 프레임 무시")

        logger.info(f"[TRACK 종료] kind={track.kind}")

    # ── 연결 상태 ────────────────────────────────────

    def _on_connection_state_change(self) -> None:
        state = self.peer.connectionState
        logger.info(f"[연결 상태] {state}")
        if state == "connected":
            try:
                self.stomp_ws.close()
            except Exception:
                pass
        elif state in ("failed", "closed"):
            from . import remove_session
            remove_session(self.room_id)

    # ── SDP 핸들링 ───────────────────────────────────

    async def handle_offer(self, payload: dict) -> None:
        offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        await self.peer.setRemoteDescription(offer)
        logger.info("[SDP] Remote description 설정 완료")

        # 버퍼링된 ICE 후보 처리
        self._remote_desc_set = True
        if self._pending_remote_ice:
            logger.info(f"[ICE] 버퍼링된 후보 {len(self._pending_remote_ice)}개 처리")
            for ice_payload in self._pending_remote_ice:
                try:
                    candidate = _parse_candidate(ice_payload["candidate"])
                    candidate.sdpMid = ice_payload.get("sdpMid")
                    candidate.sdpMLineIndex = ice_payload.get("sdpMLineIndex")
                    await self.peer.addIceCandidate(candidate)
                    logger.info(f"[ICE] 버퍼링 후보 추가: {ice_payload['candidate'][:60]}")
                except Exception as e:
                    logger.error(f"[ICE] 버퍼링 후보 추가 실패: {e}")
            self._pending_remote_ice.clear()

        # TTS 오디오 트랙 추가 (answer 생성 전에 추가해야 SDP에 포함)
        self.peer.addTrack(self._tts_track)

        answer = await self.peer.createAnswer()
        await self.peer.setLocalDescription(answer)
        logger.info("[SDP] Local description(answer) 설정 완료")

        # ICE gathering 완료 대기 (최대 10초)
        for _ in range(200):
            if self.peer.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.05)
        logger.info(f"[ICE] Gathering state: {self.peer.iceGatheringState}")

        # Answer 전송
        stomp.send(self.stomp_ws, "/app/signal/webrtc/answer", {
            "type": "WEBRTC_ANSWER",
            "roomId": self.room_id,
            "payload": {"sdp": self.peer.localDescription.sdp, "type": self.peer.localDescription.type},
        })
        logger.info("[SDP] Answer 전송 완료")

        # AI 서버의 ICE 후보를 프론트엔드로 전송
        await self._send_local_candidates()

    async def handle_ice(self, payload: dict) -> None:
        if not self._remote_desc_set:
            logger.info("[ICE] Remote description 미설정 — 후보 버퍼링")
            self._pending_remote_ice.append(payload)
            return

        try:
            candidate = _parse_candidate(payload["candidate"])
            candidate.sdpMid = payload.get("sdpMid")
            candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
            await self.peer.addIceCandidate(candidate)
            logger.info(f"[ICE] 원격 후보 추가: {payload['candidate'][:60]}")
        except Exception as e:
            logger.error(f"[ICE] 원격 후보 추가 실패: {e}")

    # ── 리소스 정리 ──────────────────────────────────

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True

        logger.info(f"[세션 정리] room={self.room_id}")

        # 타이머 취소
        if self._interview_timer:
            self._interview_timer.cancel()
            self._interview_timer = None
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
            self._ptt_timeout_timer = None

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

        # GC 강제 실행
        gc.collect()
        logger.info(f"[세션 정리 완료] room={self.room_id}")


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
