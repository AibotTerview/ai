import time
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from websocket import WebSocket
from . import stomp
from aiortc.contrib.media import MediaRecorder
from ..storage import upload_file_to_s3
from ..db import save_interview_result
from ..stt import transcribe_audio
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
        self.peer.on("icegatheringstatechange", self._on_ice_gathering_state_change)
        self.peer.on("iceconnectionstatechange", self._on_ice_connection_state_change)

        # 미디어 녹화 설정
        self.recorder = MediaRecorder(f"interview_{room_id}.mp4")
        self.audio_recorder = MediaRecorder(f"interview_{room_id}.wav")
        self.recording_started = False  # 녹화 시작 플래그
        self._start_lock = asyncio.Lock()  # 동시성 제어
        self._closed = False  # 정리 완료 플래그

        # 실시간 STT를 위한 청크 녹화 설정
        self.chunk_index = 0
        self.chunk_recorder = None
        self.chunk_track = None
        self.transcribed_texts = []  # 실시간 변환된 텍스트 누적
        self.last_stt_time = time.time()

        # 연결 타임아웃 설정 (30초)
        asyncio.create_task(self._connection_timeout())

    def _on_ice_candidate(self, candidate: RTCIceCandidate) -> None:
        if candidate:
            logger.info(f"[ICE Candidate 생성] {candidate.candidate}")
            stomp.send(self.stomp_ws, "/app/signal/webrtc/ice", {
                "type": "ICE_CANDIDATE",
                "roomId": self.room_id,
                "payload": {
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            })
        else:
            logger.info("[ICE Candidate] 모든 candidate 수집 완료 (null candidate)")

    async def _on_track(self, track: MediaStreamTrack) -> None:
        logger.info(f"[TRACK 수신] kind={track.kind}, id={track.id}, readyState={track.readyState}")

        # 오디오 트랙만 녹화
        if track.kind == "audio":
            async with self._start_lock:
                self.audio_recorder.addTrack(track)
                self.recorder.addTrack(track)

                # 첫 트랙일 때만 녹화 시작
                if not self.recording_started:
                    await self.recorder.start()
                    await self.audio_recorder.start()
                    self.recording_started = True
                    logger.info("[녹화 시작]")

                # 실시간 STT를 위한 청크 녹화 시작
                asyncio.create_task(self._realtime_stt_loop(track))
        elif track.kind == "video":
            logger.warning(f"[VIDEO 트랙] 프레임 소비만 함 (녹화 안 함)")

        # 프레임 수신 루프 (비디오도 소비해서 버퍼 방지)
        frame_count = 0
        start_time = time.time()

        while True:
            try:
                frame = await track.recv()
                frame_count += 1
                elapsed = time.time() - start_time

                if track.kind == "audio":
                    # 30초마다 한 번만 로깅 (50fps 기준 약 1500프레임)
                    if frame_count % 1500 == 0:
                        fps = frame_count / elapsed if elapsed > 0 else 0
                        logger.info(
                            f"[AUDIO] {frame_count}프레임 수신, "
                            f"{elapsed:.1f}초 경과, "
                            f"평균 {fps:.1f}fps"
                        )
                elif track.kind == "video":
                    # 30초마다 한 번만 로깅 (30fps 기준 약 900프레임)
                    if frame_count % 900 == 0:
                        logger.info(f"[VIDEO] {frame_count}프레임 수신, {elapsed:.1f}초 경과")

            except Exception as e:
                logger.error(f"[TRACK 에러] kind={track.kind}, error={e}")
                break

        logger.info(f"[TRACK 종료] kind={track.kind}, 총 {frame_count}프레임 수신")

    async def _on_ice_gathering_state_change(self) -> None:
        logger.info(f"[ICE Gathering State] {self.peer.iceGatheringState}")

    async def _on_ice_connection_state_change(self) -> None:
        logger.info(f"[ICE Connection State] {self.peer.iceConnectionState}")
        if self.peer.iceConnectionState == "failed":
            logger.error("[ICE 연결 실패] NAT 통과 실패 또는 네트워크 문제")

    async def _on_connection_state_change(self) -> None:
        logger.info(f"[Connection State] {self.peer.connectionState}")

        if self.peer.connectionState == "connected":
            logger.info("[WebRTC 연결 성공!]")
            self.stomp_ws.close()
        elif self.peer.connectionState in ["failed", "closed"]:
            logger.warning(f"[WebRTC 연결 종료] state={self.peer.connectionState}")

            # 녹화가 시작되었을 때만 stop
            if self.recording_started:
                await self.recorder.stop()
                await self.audio_recorder.stop()

                # 별도 스레드나 비동기 태스크로 업로드 진행
                asyncio.create_task(self._upload_and_save())

            # PeerConnection 정리
            await self.close()

    async def _upload_and_save(self) -> None:
        logger.info("[Upload] s3 upload start...")

        video_path = f"interview_{self.room_id}.mp4"
        audio_path = f"interview_{self.room_id}.wav"

        # 파일 존재 여부 확인
        if not os.path.exists(video_path) or not os.path.exists(audio_path):
            logger.warning(f"[Upload] 파일이 존재하지 않음: video={os.path.exists(video_path)}, audio={os.path.exists(audio_path)}")
            return

        # Calculate duration
        duration = 0
        if hasattr(self, 'start_time'):
            duration = time.time() - self.start_time

        # 1. STT 처리 (오디오 → 텍스트)
        logger.info("[STT] Starting transcription...")
        transcribed_text = transcribe_audio(audio_path)

        if transcribed_text:
            logger.info(f"[STT] Transcription completed: {len(transcribed_text)} characters")
            logger.info(f"[STT] Preview: {transcribed_text[:200]}...")
        else:
            logger.warning("[STT] Transcription failed or skipped")
            transcribed_text = "STT 변환 실패 또는 API 키 없음"

        # 2. Upload to S3
        video_url = upload_file_to_s3(video_path, "video/mp4")
        audio_url = upload_file_to_s3(audio_path, "audio/wav")

        # 3. Save metadata to DB
        if video_url and audio_url:
            save_interview_result(self.room_id, video_url, audio_url, duration, transcribed_text)

        # 4. Clean up local files
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
        logger.info(f"[ICE Candidate 수신] {payload['candidate']}")
        candidate = _parse_candidate(payload["candidate"])
        candidate.sdpMid = payload["sdpMid"]
        candidate.sdpMLineIndex = payload["sdpMLineIndex"]
        await self.peer.addIceCandidate(candidate)
        logger.info(f"[ICE Candidate 추가 완료] IP={candidate.ip}, port={candidate.port}, type={candidate.type}")

    async def _connection_timeout(self) -> None:
        """30초 후에도 연결 안 되면 자동 종료"""
        await asyncio.sleep(30)
        if self.peer.connectionState not in ["connected", "closed", "failed"]:
            logger.warning(f"[연결 타임아웃] 30초 경과, state={self.peer.connectionState}")
            await self.close()

    async def _realtime_stt_loop(self, track: MediaStreamTrack) -> None:
        """실시간 STT: 10초 청크마다 오디오를 변환"""
        from ..stt import transcribe_audio

        CHUNK_DURATION = 10  # 10초마다 STT 수행

        while not self._closed:
            try:
                # 새 청크 녹화 시작
                chunk_file = f"chunk_{self.room_id}_{self.chunk_index}.wav"
                chunk_recorder = MediaRecorder(chunk_file)
                chunk_recorder.addTrack(track)
                await chunk_recorder.start()

                logger.debug(f"[청크 녹화 시작] chunk {self.chunk_index}")

                # 10초 대기
                await asyncio.sleep(CHUNK_DURATION)

                # 청크 녹화 중지
                await chunk_recorder.stop()
                logger.debug(f"[청크 녹화 완료] chunk {self.chunk_index}")

                if self._closed:
                    if os.path.exists(chunk_file):
                        os.remove(chunk_file)
                    break

                # STT 수행 (비동기로 실행하여 메인 루프 블로킹 방지)
                if os.path.exists(chunk_file):
                    logger.info(f"[실시간 STT] 청크 #{self.chunk_index} 변환 중...")

                    loop = asyncio.get_event_loop()
                    text = await loop.run_in_executor(None, transcribe_audio, chunk_file)

                    if text and text.strip():
                        self.transcribed_texts.append(text.strip())
                        elapsed = time.time() - self.last_stt_time
                        logger.info(f"[실시간 STT 결과] (+{elapsed:.1f}초) {text.strip()}")
                        self.last_stt_time = time.time()
                    else:
                        logger.debug(f"[실시간 STT] 청크 #{self.chunk_index} - 무음 또는 변환 실패")

                    # 청크 파일 삭제
                    os.remove(chunk_file)

                self.chunk_index += 1

            except Exception as e:
                logger.error(f"[실시간 STT 에러] {e}")
                # 에러 발생해도 계속 시도
                if os.path.exists(chunk_file):
                    try:
                        os.remove(chunk_file)
                    except:
                        pass
                await asyncio.sleep(1)  # 잠시 대기 후 재시도

    async def close(self) -> None:
        """리소스 정리"""
        if self._closed:
            return

        self._closed = True
        logger.info("[WebRTC 세션 정리 시작]")

        # 전체 변환된 텍스트 출력
        if self.transcribed_texts:
            full_text = " ".join(self.transcribed_texts)
            logger.info(f"[전체 STT 결과] {full_text}")

        try:
            if self.peer:
                await self.peer.close()
                logger.info("[PeerConnection 종료 완료]")
        except Exception as e:
            logger.error(f"[정리 중 에러] {e}")


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
