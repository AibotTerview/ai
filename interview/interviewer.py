import json
import logging
import asyncio
from functools import partial
import google.generativeai as genai
from django.conf import settings

from interview.personas import PersonaService
from interview.context import LLMContextService
from interview.schemas import LLM_RESPONSE_JSON_SCHEMA

logger = logging.getLogger(__name__)

# ── Gemini 클라이언트 ─────────────────────────────────

_configured = False

def _build_contents(messages: list[dict]) -> list:
    """메시지 리스트를 Gemini API contents 형식으로 변환"""
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [msg["text"]]})
    return contents


def _call_gemini_json_sync(system_prompt: str, messages: list[dict]) -> dict:
    """동기 Gemini API 호출 — JSON 스키마 응답 (스레드에서 실행)"""
    global _configured
    if not _configured:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _configured = True

    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=system_prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.7,
            max_output_tokens=300,
            response_mime_type="application/json",
            response_schema=LLM_RESPONSE_JSON_SCHEMA,
        ),
    )
    contents = _build_contents(messages)
    response = model.generate_content(contents)
    text = getattr(response, "text", None) or ""
    if not text.strip():
        raise ValueError("Gemini returned empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"[Gemini] Invalid JSON: {e}, raw text: {text[:200]!r}")
        raise ValueError(f"Gemini response was not valid JSON: {e}") from e


async def call_gemini_json(system_prompt: str, messages: list[dict]) -> dict:
    """비동기 Gemini API 호출 — JSON 스키마 응답"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(_call_gemini_json_sync, system_prompt, messages)
    )


class InterviewSession:
    """면접 세션 — 대화 히스토리 관리 및 LLM 기반 질문 생성"""

    def __init__(
        self,
        persona: str = "FORMAL",
        max_questions: int = 8,
        setting_id: str | None = None,
    ) -> None:
        self.persona = persona.upper()
        self.max_questions = max_questions
        self.setting_id = setting_id
        self.question_count = 0
        self.history: list[dict[str, str]] = []  # [{"role": "interviewer"|"user", "text": "..."}]
        self.finished = False

        self._setting_context = ""
        if setting_id:
            try:
                self._setting_context = LLMContextService.get_setting_context(setting_id)
            except Exception as e:
                logger.warning(f"[InterviewSession] setting_id={setting_id} 컨텍스트 로드 실패: {e}")

        logger.info(
            f"[InterviewSession] 생성 — persona={self.persona}, max_questions={self.max_questions}, setting_id={setting_id}"
        )

    def add_question(self, text: str) -> None:
        self.history.append({"role": "interviewer", "text": text})
        self.question_count += 1

    def add_answer(self, text: str) -> None:
        self.history.append({"role": "user", "text": text})

    def is_last_question(self) -> bool:
        return self.question_count >= self.max_questions

    def _build_system_prompt(self) -> str:
        try:
            persona_text = PersonaService.get_persona(self.persona)
        except KeyError:
            persona_text = PersonaService.get_persona(None)  # DEFAULT_PERSONA (FORMAL)
        prompt = (
            f"{persona_text}\n\n"
            f"면접 진행 규칙:\n"
            f"- 한 번에 하나의 질문만 합니다.\n"
            f"- 질문은 간결하게 1~3문장으로 합니다.\n"
            f"- 현재 {self.question_count}/{self.max_questions}번째 질문입니다."
        )
        if self._setting_context:
            prompt += "\n\n[면접 설정 정보 (아래 내용을 참고해서 질문을 생성해)]\n"
            prompt += self._setting_context
        return prompt

    @staticmethod
    def _json_to_result(data: dict, finished: bool = False) -> dict:
        """JSON 응답을 handler 기대 형식으로 변환 (next_question→text, face→expression)"""
        return {
            "text": data["next_question"],
            "expression": data["face"],
            "finished": finished,
        }

    async def generate_first_question(self) -> dict:
        """첫 질문 생성 (자기소개 요청)"""
        system_prompt = self._build_system_prompt()
        messages = [
            {"role": "user", "text": "첫 번째 면접 질문을 생성해주세요.\n\n현재 질문 순서: 1번째"}
        ]

        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=False)
        self.add_question(result["text"])

        logger.info(f"[Interview] 첫 질문: {result['text'][:80]}... [{result['expression']}]")
        return result

    async def process_answer(self, user_text: str) -> dict:
        """사용자 답변을 분석하고 다음 질문(또는 마무리)을 생성

        Returns:
            {"text": str, "expression": str, "finished": bool}
        """
        self.add_answer(user_text)

        # 마지막 질문이었으면 마무리 멘트 생성
        if self.is_last_question():
            return await self._generate_closing()

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
            "text": (
                "위 답변을 바탕으로 다음 면접 질문을 해 주세요.\n\n"
                f"현재 질문 순서: {self.question_count + 1}번째"
            ),
        })

        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=False)
        self.add_question(result["text"])

        logger.info(
            f"[Interview] Q{self.question_count}: {result['text'][:80]}... [{result['expression']}]"
        )
        return result

    async def _generate_closing(self) -> dict:
        """면접 마무리 멘트 생성"""
        system_prompt = self._build_system_prompt()

        messages = []
        for entry in self.history:
            messages.append({"role": entry["role"], "text": entry["text"]})

        messages.append({
            "role": "user",
            "text": (
                "모든 질문이 끝났습니다. "
                "면접을 마무리하는 인사를 해 주세요. "
                "수고했다는 격려와 함께 짧게 마무리합니다. "
                "next_question에 마무리 멘트를 작성하고, 새로운 질문은 하지 마세요."
            ),
        })

        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=True)
        self.finished = True

        logger.info(f"[Interview] 마무리: {result['text'][:80]}... [{result['expression']}]")
        return result
