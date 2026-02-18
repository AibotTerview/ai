import asyncio

from interview.interviewer import InterviewSession
from speech.tts import synthesize as tts_synthesize
from interview.signals import answer_submitted

INTERVIEW_MAX_DURATION = 30 * 60
PTT_NO_RESPONSE_TIMEOUT = 2 * 60


class InterviewMixin:

    def _start_interview_timer(self) -> None:
        loop = asyncio.get_event_loop()
        self._interview_timer = loop.call_later(INTERVIEW_MAX_DURATION, self._on_interview_timeout)

    def _reset_ptt_timeout(self) -> None:
        if self._ptt_timeout_timer:
            self._ptt_timeout_timer.cancel()
        loop = asyncio.get_event_loop()
        self._ptt_timeout_timer = loop.call_later(PTT_NO_RESPONSE_TIMEOUT, self._on_ptt_timeout)

    def _on_interview_timeout(self) -> None:
        self.send_dc({"type": "INTERVIEW_END", "text": "면접 시간이 초과되어 자동 종료됩니다.", "expression": "neutral"})
        from signaling.session import remove_session
        remove_session(self.room_id)

    def _on_ptt_timeout(self) -> None:
        self.send_dc({"type": "AI_ERROR", "message": "응답이 없어 면접이 종료됩니다."})
        from signaling.session import remove_session
        remove_session(self.room_id)

    async def _start_interview(self, persona: str, max_questions: int) -> None:
        self._interview = InterviewSession(persona=persona, max_questions=max_questions)

        result = await self._interview.generate_first_question()
        await self.send_dc_async({
            "type": "AI_QUESTION",
            "text": result["text"],
            "expression": result["expression"],
            "questionNumber": self._interview.question_count,
            "totalQuestions": self._interview.max_questions,
        })
        await self._speak(result["text"])

    async def _handle_interview_answer(self, user_text: str) -> None:
        last_question = ""
        if self._interview.history:
            last_entry = self._interview.history[-1]
            if last_entry.get("role") == "interviewer":
                last_question = last_entry.get("text", "")

        answer_submitted.send(
            sender=None,
            interview_id=self.room_id,
            sequence=self._interview.question_count,
            question=last_question,
            answer=user_text,
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

    async def _speak(self, text: str) -> None:
        pcm_bytes = await tts_synthesize(text, gender=self._gender)
        await self._tts_track.play(pcm_bytes)
        self.send_dc({"type": "AI_DONE"})
        self._reset_ptt_timeout()
