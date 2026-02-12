import re
import logging
import asyncio
from functools import partial
import google.generativeai as genai
from django.conf import settings

logger = logging.getLogger(__name__)

# ── Gemini 클라이언트 (싱글턴) ─────────────────────────

_model = None


def _get_model():
    global _model
    if _model is None:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _model = genai.GenerativeModel("gemini-2.0-flash")
    return _model


def _call_gemini_sync(system_prompt: str, messages: list[dict]) -> str:
    """동기 Gemini API 호출 (스레드에서 실행)"""
    model = _get_model()

    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [msg["text"]]})

    response = model.generate_content(
        contents,
        generation_config=genai.types.GenerationConfig(
            temperature=0.7,
            max_output_tokens=300,
        ),
        system_instruction=system_prompt,
    )
    return response.text


async def call_gemini(system_prompt: str, messages: list[dict]) -> str:
    """비동기 Gemini API 호출"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(_call_gemini_sync, system_prompt, messages)
    )

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

    def _build_system_prompt(self) -> str:
        persona_text = PERSONA_PROMPTS.get(self.persona, PERSONA_PROMPTS["FORMAL"])
        return (
            f"{persona_text}\n\n"
            f"면접 진행 규칙:\n"
            f"- 한 번에 하나의 질문만 합니다.\n"
            f"- 질문은 간결하게 1~3문장으로 합니다.\n"
            f"- 현재 {self.question_count}/{self.max_questions}번째 질문입니다.\n\n"
            f"{EXPRESSION_GUIDE}"
        )

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """LLM 응답에서 텍스트와 표정 태그를 분리"""
        match = re.search(r"\[expression:(\w+)]", raw)
        expression = match.group(1) if match else "neutral"
        text = re.sub(r"\s*\[expression:\w+]\s*", "", raw).strip()
        return {"text": text, "expression": expression}

    async def generate_first_question(self) -> dict:
        """첫 질문 생성 (자기소개 요청)"""
        system_prompt = self._build_system_prompt()
        messages = [
            {"role": "user", "text": "면접을 시작합니다. 첫 질문으로 자기소개를 요청해 주세요."}
        ]

        raw = await call_gemini(system_prompt, messages)
        result = self._parse_response(raw)
        self.add_question(result["text"])

        logger.info(f"[Interview] 첫 질문: {result['text'][:80]}... [{result['expression']}]")
        return result

    async def process_answer(self, user_text: str) -> dict:
        """사용자 답변을 분석하고 다음 질문(또는 마무리)을 생성"""
        self.add_answer(user_text)

        # 답변 간단 분석 → 프롬프트에 힌트 제공
        analysis_hints = []
        if len(user_text) < 20:
            analysis_hints.append("지원자의 답변이 매우 짧습니다. 더 구체적으로 답변하도록 유도해 주세요.")
        if len(user_text) > 500:
            analysis_hints.append("지원자의 답변이 길었습니다. 핵심을 요약하도록 유도할 수 있습니다.")

        hint_text = "\n".join(analysis_hints)

        system_prompt = self._build_system_prompt()
        if hint_text:
            system_prompt += f"\n\n분석 참고:\n{hint_text}"

        # 대화 히스토리를 Gemini에 전달
        messages = []
        for entry in self.history:
            messages.append({"role": entry["role"], "text": entry["text"]})

        # 다음 질문 요청 추가
        messages.append({
            "role": "user",
            "text": "위 답변을 바탕으로 다음 면접 질문을 해 주세요.",
        })

        raw = await call_gemini(system_prompt, messages)
        result = self._parse_response(raw)
        self.add_question(result["text"])

        logger.info(
            f"[Interview] Q{self.question_count}: {result['text'][:80]}... [{result['expression']}]"
        )
        return result
