import logging

logger = logging.getLogger(__name__)


class InterviewSession:
    """면접 세션 — 대화 히스토리 관리 및 LLM 기반 질문 생성"""

    def __init__(self, persona: str = "FORMAL", max_questions: int = 8) -> None:
        self.persona = persona.upper()
        self.max_questions = max_questions
        self.question_count = 0
        self.history: list[dict[str, str]] = []  # [{"role": "interviewer"|"user", "text": "..."}]
        self.finished = False

        logger.info(
            f"[InterviewSession] 생성 — persona={self.persona}, max_questions={self.max_questions}"
        )

    def add_question(self, text: str) -> None:
        self.history.append({"role": "interviewer", "text": text})
        self.question_count += 1

    def add_answer(self, text: str) -> None:
        self.history.append({"role": "user", "text": text})

    def is_last_question(self) -> bool:
        return self.question_count >= self.max_questions
