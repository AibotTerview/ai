import logging
import asyncio

from ..interviewer import InterviewSession
from ..tts import synthesize as tts_synthesize
from ..signals import answer_submitted

logger = logging.getLogger(__name__)

# 타임아웃 설정 (초)
INTERVIEW_MAX_DURATION = 30 * 60  # 면접 최대 30분
PTT_NO_RESPONSE_TIMEOUT = 2 * 60  # PTT 무응답 타임아웃 2분


class InterviewMixin:
    """면접 세션 오케스트레이션 + 타임아웃 관리 mixin — WebRTCSession에서 사용"""

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
        """사용자 답변 → LLM → 다음 질문 또는 종료"""
        # 답변 제출 시그널 발송
        try:
            last_question = ""
            if self._interview and self._interview.history:
                last_entry = self._interview.history[-1]
                if last_entry.get('role') == 'interviewer':
                    last_question = last_entry.get('text', "")

            sequence = self._interview.question_count if self._interview else 0

            answer_submitted.send(
                sender=None,
                interview_id=self.room_id,
                sequence=sequence,
                question=last_question,
                answer=user_text
            )
            logger.info(f"[Signal] answer_submitted 발송 완료: room={self.room_id}, seq={sequence}")

        except Exception as e:
            logger.error(f"[Signal] answer_submitted 발송 실패: {e}")

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
