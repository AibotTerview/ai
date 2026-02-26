import asyncio
import time

from asgiref.sync import sync_to_async
from interview.interviewer import InterviewSession
from interview.models import InterviewSetting
from speech.tts import synthesize as tts_synthesize
from interview.signals import answer_submitted, interview_ended

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

    def _get_elapsed_seconds(self) -> int:
        """면접 시작부터 현재까지 경과한 시간을 초 단위로 반환."""
        if hasattr(self, '_interview_start_time') and self._interview_start_time:
            return int(time.time() - self._interview_start_time)
        return 0

    def _fire_interview_ended(self) -> None:
        """interview_ended 시그널 발신."""
        duration = self._get_elapsed_seconds()
        interview_ended.send(
            sender=None,
            interview_id=self.room_id,
            duration=duration,
        )

    # 인터뷰 타이머 초과 할 떄의 처리
    def _on_interview_timeout(self) -> None:
        # datachannel.py에 있음
        self.send_dc({"type": "INTERVIEW_END", "text": "면접 시간이 초과되어 자동 종료됩니다.", "expression": "neutral"})
        self._fire_interview_ended()
        from signaling.session import remove_session
        remove_session(self.room_id)

    # PTT 타이머 초과 할 때의 처리
    def _on_ptt_timeout(self) -> None:
        self.send_dc({"type": "AI_ERROR", "message": "응답이 없어 면접이 종료됩니다."})
        self._fire_interview_ended()
        from signaling.session import remove_session
        remove_session(self.room_id)

    async def _start_interview(self) -> None:
        setting: InterviewSetting = await sync_to_async(InterviewSetting.objects.get)(setting_id=self.room_id)
        persona = setting.interviewer_style
        max_questions = setting.question_count
        self._gender = setting.interviewer_gender.lower()

        self._interview = InterviewSession(persona=persona, max_questions=max_questions, setting_id=self.room_id)
        await self._interview.async_setup()

        # 면접 시작 시각 기록
        self._interview_start_time = time.time()

        # Interview 레코드 생성 (없으면 생성, 있으면 무시)
        from django.utils import timezone
        from interview.models import Interview
        await sync_to_async(Interview.objects.get_or_create)(
            setting=setting,
            defaults={
                'interview_id': self.room_id,
                'created_at': timezone.now(),
            }
        )

        from speech.recorder import InterviewRecorder
        self._recorder = InterviewRecorder(self.room_id)
        self._recorder.start()

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
        )

        result = await self._interview.process_answer(user_text)

        if result["finished"]:
            self.send_dc({"type": "INTERVIEW_END", "text": result["text"], "expression": result["expression"]})
            self._fire_interview_ended()
            await self._speak(result["text"])
            # PTT 타임아웃 타이머 취소 (클로징 이후 오발송 방지)
            if self._ptt_timeout_timer:
                self._ptt_timeout_timer.cancel()
                self._ptt_timeout_timer = None
            # 클로징 TTS가 프론트에 전달될 시간 확보 후 세션 종료
            asyncio.get_event_loop().call_later(1.0, self._deferred_remove_session)
        else:
            self.send_dc({
                "type": "AI_QUESTION",
                "text": result["text"],
                "expression": result["expression"],
                "questionNumber": self._interview.question_count,
                "totalQuestions": self._interview.max_questions,
            })
            await self._speak(result["text"])

    def _deferred_remove_session(self) -> None:
        from signaling.session import remove_session
        remove_session(self.room_id)

    async def _speak(self, text: str) -> None:
        pcm_bytes = await tts_synthesize(text, gender=self._gender)
        if self._recorder:
            self._recorder.push_audio_pcm(pcm_bytes)
        await self._tts_track.play(pcm_bytes)
        self.send_dc({"type": "AI_DONE"})
        self._reset_ptt_timeout()
