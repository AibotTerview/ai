import gc
import io
import json
import wave
import logging
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.mediastreams import MediaStreamTrack
from websocket import WebSocket
from . import stomp
from ..stt import transcribe as stt_transcribe
from ..tts import synthesize as tts_synthesize
from ..interviewer import InterviewSession
from ..audio_track import TTSAudioTrack

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# PTT 오디오 버퍼 최대 크기 (3분 @ 48kHz mono 16bit ≈ 17MB)
MAX_AUDIO_BUFFER_BYTES = 18 * 1024 * 1024

# 타임아웃 설정 (초)
INTERVIEW_MAX_DURATION = 30 * 60  # 면접 최대 30분
PTT_NO_RESPONSE_TIMEOUT = 2 * 60  # PTT 무응답 타임아웃 2분
PTT_MAX_RECORDING_DURATION = 3 * 60  # PTT 최대 녹음 3분


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

        # PTT 오디오 버퍼링
        self._ptt_active = False
        self._audio_frames: list[bytes] = []
        self._audio_buffer_size = 0
        self._audio_sample_rate = 48000
        self._audio_channels = 1

        # 콜백: PTT_END 시 WAV 데이터를 처리하는 함수 (외부에서 설정)
        self.on_ptt_audio = None  # async def callback(wav_bytes: bytes)

        # 면접 세션 (DataChannel 연결 시 초기화)
        self._interview: InterviewSession | None = None

        # TTS 오디오 트랙 (offer 처리 시 peer에 추가)
        self._tts_track = TTSAudioTrack()
        self._gender = "male"

        # 세션 상태
        self._cleaned_up = False
        self._interview_timer: asyncio.TimerHandle | None = None
        self._ptt_timeout_timer: asyncio.TimerHandle | None = None
        self._ptt_recording_start: float = 0.0

    # ── 타임아웃 관리 ──────────────────────────────────

    def _start_interview_timer(self) -> None:
        """면접 최대 시간 타이머 시작 (30분)"""
        loop = asyncio.get_event_loop()
        self._interview_timer = loop.call_later(
            INTERVIEW_MAX_DURATION, self._on_interview_timeout
        )
        logger.info(f"[타이머] 면접 타이머 시작: {INTERVIEW_MAX_DURATION}초")

    def _reset_ptt_timeout(self) -> None:
        """PTT 무응답 타이머 리셋 (2분)"""
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
        loop = asyncio.get_event_loop()
        self._ptt_timeout_timer = loop.call_later(
            PTT_NO_RESPONSE_TIMEOUT, self._on_ptt_timeout
        )

    def _on_interview_timeout(self) -> None:
        """면접 최대 시간 초과"""
        logger.warning(f"[타임아웃] 면접 최대 시간({INTERVIEW_MAX_DURATION}초) 초과, room={self.room_id}")
        self.send_dc({
            "type": "INTERVIEW_END",
            "text": "면접 시간이 초과되어 자동 종료됩니다.",
            "expression": "neutral",
        })
        from . import remove_session
        remove_session(self.room_id)

    def _on_ptt_timeout(self) -> None:
        """PTT 무응답 타임아웃"""
        logger.warning(f"[타임아웃] PTT 무응답({PTT_NO_RESPONSE_TIMEOUT}초) 초과, room={self.room_id}")
        self.send_dc({
            "type": "AI_ERROR",
            "message": "응답이 없어 면접이 종료됩니다.",
        })
        from . import remove_session
        remove_session(self.room_id)

    # ── DataChannel ─────────────────────────────────

    def _on_datachannel(self, channel) -> None:
        logger.info(f"[DataChannel 수신] label={channel.label}")
        self._dc = channel
        self._dc.on("message", self._on_dc_message)
        self._dc.on("close", self._on_dc_close)

        # 면접 세션 시작 + 타이머
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(self._start_interview(), loop=loop)
        self._start_interview_timer()

    def _on_dc_message(self, message) -> None:
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[DC] 파싱 실패: {message}")
            return

        msg_type = data.get("type")
        logger.info(f"[DC 수신] type={msg_type}")

        if msg_type == "PTT_START":
            self._start_recording()
        elif msg_type == "PTT_END":
            self._stop_recording()

    def _on_dc_close(self) -> None:
        logger.info("[DataChannel 종료]")
        self._dc = None

    def send_dc(self, data: dict) -> None:
        """DataChannel로 JSON 메시지 전송"""
        if self._dc and self._dc.readyState == "open":
            self._dc.send(json.dumps(data, ensure_ascii=False))
        else:
            logger.warning(f"[DC] 채널이 열려있지 않아 전송 불가: {data.get('type')}")

    async def send_dc_async(self, data: dict, timeout: float = 10.0) -> bool:
        """DataChannel이 열릴 때까지 대기 후 전송"""
        elapsed = 0.0
        while elapsed < timeout:
            if self._dc and self._dc.readyState == "open":
                self._dc.send(json.dumps(data, ensure_ascii=False))
                return True
            await asyncio.sleep(0.1)
            elapsed += 0.1
        logger.warning(f"[DC] 타임아웃 — 전송 실패: {data.get('type')}")
        return False

    # ── PTT 오디오 버퍼링 ───────────────────────────

    def _start_recording(self) -> None:
        logger.info("[PTT] 녹음 시작")
        self._ptt_active = True
        self._audio_frames.clear()
        self._audio_buffer_size = 0
        self._ptt_recording_start = asyncio.get_event_loop().time()
        # PTT 무응답 타이머 취소 (사용자가 응답함)
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
            self._ptt_timeout_timer = None

    def _stop_recording(self) -> None:
        logger.info(f"[PTT] 녹음 종료 — {len(self._audio_frames)}프레임, {self._audio_buffer_size}bytes")
        self._ptt_active = False

        if not self._audio_frames:
            logger.warning("[PTT] 오디오 프레임 없음, 무시")
            return

        wav_bytes = self._frames_to_wav()
        self._audio_frames.clear()
        self._audio_buffer_size = 0

        # STT 파이프라인 실행
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(self._process_stt(wav_bytes), loop=loop)

    async def _process_stt(self, wav_bytes: bytes) -> None:
        """WAV → Whisper STT → DataChannel로 결과 전송 → LLM 면접관"""
        text = await stt_transcribe(wav_bytes)
        self.send_dc({"type": "USER_STT", "text": text})
        logger.info(f"[STT→DC] USER_STT 전송: {text[:80]}...")

        # 면접 세션이 있으면 LLM으로 다음 질문 생성
        if self._interview and not self._interview.finished:
            await self._handle_interview_answer(text)

        # 외부 콜백이 있으면 호출
        if self.on_ptt_audio:
            await self.on_ptt_audio(text)

    # ── 면접 세션 관리 ────────────────────────────────

    async def _start_interview(self, persona: str = "FORMAL", max_questions: int = 8) -> None:
        """면접 세션 초기화 + 첫 질문 생성 + TTS 음성 전송"""
        self._interview = InterviewSession(persona=persona, max_questions=max_questions)

        try:
            result = await self._interview.generate_first_question()
            await self.send_dc_async({
                "type": "AI_QUESTION",
                "text": result["text"],
                "expression": result["expression"],
                "questionNumber": self._interview.question_count,
                "totalQuestions": self._interview.max_questions,
            })
            await self._speak(result["text"])
        except Exception as e:
            logger.error(f"[Interview] 첫 질문 생성 실패: {e}")
            self.send_dc({"type": "AI_ERROR", "message": "면접 시작에 실패했습니다."})

    async def _handle_interview_answer(self, user_text: str) -> None:
        """사용자 답변 → LLM → 다음 질문 + TTS 또는 종료"""
        try:
            result = await self._interview.process_answer(user_text)
            if result["finished"]:
                self.send_dc({
                    "type": "INTERVIEW_END",
                    "text": result["text"],
                    "expression": result["expression"],
                })
                await self._speak(result["text"])
            else:
                self.send_dc({
                    "type": "AI_QUESTION",
                    "text": result["text"],
                    "expression": result["expression"],
                    "questionNumber": self._interview.question_count,
                    "totalQuestions": self._interview.max_questions,
                })
                await self._speak(result["text"])
        except Exception as e:
            logger.error(f"[Interview] 질문 생성 실패: {e}")
            self.send_dc({"type": "AI_ERROR", "message": "질문 생성에 실패했습니다."})

    # ── TTS 음성 재생 ──────────────────────────────────

    async def _speak(self, text: str) -> None:
        """TTS 음성 생성 → WebRTC 오디오 트랙 재생 → AI_DONE 전송"""
        try:
            pcm_bytes = await tts_synthesize(text, gender=self._gender)
            await self._tts_track.play(pcm_bytes)
        except Exception as e:
            logger.error(f"[TTS] 재생 실패: {e}")
        finally:
            self.send_dc({"type": "AI_DONE"})
            # AI 응답 완료 후 PTT 무응답 타이머 시작
            self._reset_ptt_timeout()

    def _frames_to_wav(self) -> bytes:
        """누적된 오디오 프레임을 WAV 바이트로 변환"""
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(self._audio_channels)
            wf.setsampwidth(2)  # 16bit
            wf.setframerate(self._audio_sample_rate)
            for raw in self._audio_frames:
                wf.writeframes(raw)
        wav_bytes = buf.getvalue()
        buf.close()
        logger.info(f"[PTT] WAV 변환 완료: {len(wav_bytes)} bytes")
        return wav_bytes

    # ── ICE ──────────────────────────────────────────

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

        # TTS 오디오 트랙 추가 (answer 생성 전에 추가해야 SDP에 포함)
        self.peer.addTrack(self._tts_track)

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
