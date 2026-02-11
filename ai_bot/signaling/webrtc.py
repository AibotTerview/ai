import time
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from websocket import WebSocket
from . import stomp
from aiortc.contrib.media import MediaRecorder
from ..storage import upload_file_to_s3
from ..db import save_interview_result
import os
import asyncio

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
        
        # 미디어 녹화 설정
        self.recorder = MediaRecorder(f"interview_{room_id}.mp4")
        self.audio_recorder = MediaRecorder(f"interview_{room_id}.wav")

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

        if track.kind == "audio":
            self.audio_recorder.addTrack(track)
            self.recorder.addTrack(track)
        elif track.kind == "video":
            self.recorder.addTrack(track)

        await self.recorder.start()
        await self.audio_recorder.start() 

        frame_count = 0

        frame_count = 0
        frame_count = 0
        self.start_time = time.time()

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

    async def _on_connection_state_change(self) -> None:
        logger.info(f"[Connection State] {self.peer.connectionState}")

        if self.peer.connectionState == "connected":
            self.stomp_ws.close()
        elif self.peer.connectionState in ["failed", "closed"]:
            await self.recorder.stop()
            await self.audio_recorder.stop()
            
            # 별도 스레드나 비동기 태스크로 업로드 진행
            asyncio.create_task(self._upload_and_save())

    async def _upload_and_save(self) -> None:
        logger.info("[Upload] s3 upload start...")
        
        # Calculate duration
        duration = 0
        if hasattr(self, 'start_time'):
            duration = time.time() - self.start_time

        video_path = f"interview_{self.room_id}.mp4"
        audio_path = f"interview_{self.room_id}.wav"
        
        # 1. Upload to S3
        video_url = upload_file_to_s3(video_path, "video/mp4")
        audio_url = upload_file_to_s3(audio_path, "audio/wav")
        
        # 2. Save metadata to DB
        if video_url and audio_url:
            save_interview_result(self.room_id, video_url, audio_url, duration)
            
        # 3. Clean up local files
        if os.path.exists(video_path):
            os.remove(video_path)
        if os.path.exists(audio_path):
            os.remove(audio_path)

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
