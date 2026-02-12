import json
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from websocket import WebSocket
from . import stomp

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class WebRTCSession:
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

    # ── DataChannel ─────────────────────────────────

    def _on_datachannel(self, channel) -> None:
        logger.info(f"[DataChannel 수신] label={channel.label}")
        self._dc = channel
        self._dc.on("message", self._on_dc_message)
        self._dc.on("close", self._on_dc_close)

    def _on_dc_message(self, message) -> None:
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[DC] 파싱 실패: {message}")
            return

        msg_type = data.get("type")
        logger.info(f"[DC 수신] type={msg_type}")

    def _on_dc_close(self) -> None:
        logger.info("[DataChannel 종료]")
        self._dc = None

    def send_dc(self, data: dict) -> None:
        """DataChannel로 JSON 메시지 전송"""
        if self._dc and self._dc.readyState == "open":
            self._dc.send(json.dumps(data, ensure_ascii=False))
        else:
            logger.warning("[DC] 채널이 열려있지 않아 전송 불가")

    def _on_ice_candidate(self, candidate: RTCIceCandidate) -> None:
        if candidate:
            stomp.send(self.stomp_ws, "/app/signal/webrtc/ice", {
                "type": "ICE_CANDIDATE",
                "roomId": self.room_id,
                "payload": {
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            })

    async def _on_track(self, track: MediaStreamTrack) -> None:
        logger.info(f"[TRACK 수신] kind={track.kind}, id={track.id}, readyState={track.readyState}")

        frame_count = 0
        start_time = time.time()

        while True:
            try:
                frame = await track.recv()
                frame_count += 1
                elapsed = time.time() - start_time

                if track.kind == "audio":
                    # AudioFrame 로깅
                    logger.debug(
                        f"[AUDIO FRAME #{frame_count}] "
                        f"samples={frame.samples}, "
                        f"sample_rate={frame.sample_rate}Hz, "
                        f"channels={len(frame.layout.channels)}, "
                        f"format={frame.format.name}, "
                        f"pts={frame.pts}, "
                        f"elapsed={elapsed:.2f}s"
                    )

                    # 10프레임마다 요약 로깅
                    if frame_count % 10 == 0:
                        fps = frame_count / elapsed if elapsed > 0 else 0
                        logger.info(
                            f"[AUDIO 요약] 총 {frame_count}프레임 수신, "
                            f"평균 {fps:.1f}fps, "
                            f"sample_rate={frame.sample_rate}Hz"
                        )

                elif track.kind == "video":
                    # VideoFrame 로깅
                    logger.debug(
                        f"[VIDEO FRAME #{frame_count}] "
                        f"size={frame.width}x{frame.height}, "
                        f"format={frame.format.name}, "
                        f"pts={frame.pts}, "
                        f"time_base={frame.time_base}, "
                        f"elapsed={elapsed:.2f}s"
                    )

                    # 30프레임마다 요약 로깅
                    if frame_count % 30 == 0:
                        fps = frame_count / elapsed if elapsed > 0 else 0
                        logger.info(
                            f"[VIDEO 요약] 총 {frame_count}프레임 수신, "
                            f"평균 {fps:.1f}fps, "
                            f"해상도={frame.width}x{frame.height}"
                        )

            except Exception as e:
                logger.error(f"[TRACK 에러] kind={track.kind}, error={e}")
                break

        logger.info(f"[TRACK 종료] kind={track.kind}, 총 {frame_count}프레임 수신")

    def _on_connection_state_change(self) -> None:
        if self.peer.connectionState == "connected":
            self.stomp_ws.close()

    async def handle_offer(self, payload: dict) -> None:
        offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        await self.peer.setRemoteDescription(offer)

        answer = await self.peer.createAnswer()
        await self.peer.setLocalDescription(answer)

        stomp.send(self.stomp_ws, "/app/signal/webrtc/answer", {
            "type": "WEBRTC_ANSWER",
            "roomId": self.room_id,
            "payload": {"sdp": answer.sdp, "type": answer.type},
        })

    async def handle_ice(self, payload: dict) -> None:
        candidate = _parse_candidate(payload["candidate"])
        candidate.sdpMid = payload["sdpMid"]
        candidate.sdpMLineIndex = payload["sdpMLineIndex"]
        await self.peer.addIceCandidate(candidate)


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
