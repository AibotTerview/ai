import asyncio

from asgiref.sync import sync_to_async
from interview.interviewer import InterviewSession
from interview.models import InterviewSetting
from speech.tts import tts
from interview.signals import answer_submitted

INTERVIEW_MAX_DURATION = 30 * 60 # 30분
PTT_NO_RESPONSE_TIMEOUT = 2 * 60 # 2분

class InterviewMixin:

    # 각 인터뷰 시작 시 타이머 설정
    def _start_interview_timer(self) -> None:
        loop = asyncio.get_event_loop()
        self._interview_timer = loop.call_later(INTERVIEW_MAX_DURATION, self._on_interview_timeout)

    # PTT 타이머 재 설정
    def _reset_ptt_timeout(self) -> None:
        # webrtc.py에 있음
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
        loop = asyncio.get_event_loop()
        self._ptt_timeout_timer = loop.call_later(PTT_NO_RESPONSE_TIMEOUT, self._on_ptt_timeout)

    # 인터뷰 타이머 초과 할 떄의 처리
    def _on_interview_timeout(self) -> None:
        # datachannel.py에 있음
        self.send_dc({"type": "INTERVIEW_END", "text": "면접 시간이 초과되어 자동 종료됩니다.", "expression": "neutral"})
        from signaling.session import remove_session
        remove_session(self.room_id)

    # PTT 타이머 초과 할 때의 처리
    def _on_ptt_timeout(self) -> None:
        self.send_dc({"type": "AI_ERROR", "message": "응답이 없어 면접이 종료됩니다."})
        from signaling.session import remove_session
        remove_session(self.room_id)

    async def _start_interview(self) -> None:
        setting: InterviewSetting = await sync_to_async(InterviewSetting.objects.get)(setting_id=self.room_id)
        persona = setting.interviewer_style
        max_questions = setting.question_count
        self._gender = setting.interviewer_gender.lower()

        self._interview = InterviewSession(persona=persona, max_questions=max_questions, setting_id=self.room_id)
        await self._interview.async_setup()

        result = await self._interview.generate_first_question()

        await self.send_dc_async({
            "type": "AI_QUESTION",
            "text": result["text"],
            "expression": result["expression"],
            "questionNumber": self._interview.question_count,
            "totalQuestions": self._interview.max_questions,
        })
        await self._speak(result["text"])

    async def _handle_interview_answer(self, user_text: str, wav_bytes: bytes | None = None) -> None:
        last_question = ""
        last_entry = self._interview.history[-1]
        if last_entry.get("role") == "interviewer":
            last_question = last_entry.get("text", "")

        # history 스냅샷: 평가 스레드와의 race condition 방지를 위해 복사본 전달
        history_snapshot = list(self._interview.history)

        answer_submitted.send(
            sender=None,
            interview_id=self.room_id,
            sequence=self._interview.question_count,
            question=last_question,
            answer=user_text,
            history=history_snapshot,
            wav_bytes=wav_bytes,
        )

        result = await self._interview.process_answer(user_text)

        if result["finished"]:
            self.send_dc({"type": "INTERVIEW_END", "text": result["text"], "expression": result["expression"]})
        else:
            self.send_dc({
                "type": "AI_QUESTION",
                "text": result["text"],
                "expression": result["expression"],
                "questionNumber": self._interview.question_count,
                "totalQuestions": self._interview.max_questions,
            })
        await self._speak(result["text"])

    async def _speak(self, text: str):
        pcm_bytes = await tts(text, gender=self._gender)
        await self._tts_track.play(pcm_bytes)
        self.send_dc({"type": "AI_DONE"})
        self._reset_ptt_timeout()
