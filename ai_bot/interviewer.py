import logging

logger = logging.getLogger(__name__)

# ── 페르소나별 시스템 프롬프트 ─────────────────────────

PERSONA_PROMPTS: dict[str, str] = {
    "CASUAL": (
        "당신은 친근하고 편안한 분위기의 면접관입니다.\n"
        "지원자가 긴장하지 않도록 격려하며 질문합니다.\n"
        "존댓말을 사용하고, 따뜻한 톤으로 대화합니다.\n"
        "답변에 대해 긍정적인 리액션을 먼저 한 뒤 다음 질문으로 넘어갑니다.\n"
        "예: '좋은 답변이네요! 그러면 이런 경우에는 어떻게 하실 건가요?'"
    ),
    "FORMAL": (
        "당신은 전문적이고 체계적인 면접관입니다.\n"
        "논리적이고 구조화된 질문을 합니다.\n"
        "정중하지만 비즈니스 톤을 유지합니다.\n"
        "답변의 구체성과 논리성을 중시합니다.\n"
        "예: '말씀하신 경험에서 구체적으로 어떤 역할을 담당하셨나요?'"
    ),
    "PRESSURE": (
        "당신은 도전적이고 날카로운 면접관입니다.\n"
        "꼬리질문을 많이 하고 답변의 약점을 파고듭니다.\n"
        "존댓말은 사용하지만 직설적입니다.\n"
        "지원자의 논리적 허점이나 모순을 지적합니다.\n"
        "예: '방금 말씀하신 것과 앞서 말씀하신 내용이 다른 것 같은데, 어떤 게 맞나요?'"
    ),
}

EXPRESSION_GUIDE = (
    "각 응답 끝에 반드시 [expression:TAG] 형식으로 표정을 지정하세요.\n"
    "사용 가능한 TAG: neutral, smile, serious, thinking, surprised\n"
    "- neutral: 기본 표정 (일반적인 질문)\n"
    "- smile: 미소 (격려, 긍정 반응)\n"
    "- serious: 진지한 표정 (중요한 질문, 지적)\n"
    "- thinking: 생각하는 표정 (고민하는 듯한 질문)\n"
    "- surprised: 놀란 표정 (예상치 못한 답변 반응)\n"
    "예시: '자기소개 부탁드립니다. [expression:smile]'"
)


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
