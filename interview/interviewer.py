import json
import asyncio
from functools import partial
import google.generativeai as genai
from django.conf import settings

from interview.personas import PersonaService
from interview.context import LLMContextService
from interview.schemas import LLM_RESPONSE_JSON_SCHEMA

_configured = False


def _build_contents(messages: list[dict]) -> list:
    return [
        {"role": "user" if msg["role"] == "user" else "model", "parts": [msg["text"]]}
        for msg in messages
    ]


def _call_gemini_json_sync(system_prompt: str, messages: list[dict]) -> dict:
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
    response = model.generate_content(_build_contents(messages))
    return json.loads(response.text)


async def call_gemini_json(system_prompt: str, messages: list[dict]) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_call_gemini_json_sync, system_prompt, messages))


class InterviewSession:

    def __init__(self, persona: str = "FORMAL", max_questions: int = 5, setting_id: str | None = None) -> None:
        self.persona = persona.upper()
        self.max_questions = max_questions
        self.setting_id = setting_id
        self.question_count = 0
        self.history: list[dict[str, str]] = []
        self.finished = False
        self._setting_context = LLMContextService.get_setting_context(setting_id)

    def add_question(self, text: str) -> None:
        self.history.append({"role": "interviewer", "text": text})
        self.question_count += 1

    def add_answer(self, text: str) -> None:
        self.history.append({"role": "user", "text": text})

    def is_last_question(self) -> bool:
        return self.question_count >= self.max_questions

    def _build_system_prompt(self) -> str:
        persona_text = PersonaService.get_persona(self.persona)
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
        return {"text": data["next_question"], "expression": data["face"], "finished": finished}

    async def generate_first_question(self) -> dict:
        system_prompt = self._build_system_prompt()
        messages = [{"role": "user", "text": "첫 번째 면접 질문을 생성해주세요.\n\n현재 질문 순서: 1번째"}]
        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=False)
        self.add_question(result["text"])
        return result

    async def process_answer(self, user_text: str) -> dict:
        self.add_answer(user_text)

        if self.is_last_question():
            return await self._generate_closing()

        analysis_hints = []
        if len(user_text) < 20:
            analysis_hints.append("지원자의 답변이 매우 짧습니다. 더 구체적으로 답변하도록 유도해 주세요.")
        if len(user_text) > 500:
            analysis_hints.append("지원자의 답변이 길었습니다. 핵심을 요약하도록 유도할 수 있습니다.")

        system_prompt = self._build_system_prompt()
        if analysis_hints:
            system_prompt += "\n\n분석 참고:\n" + "\n".join(analysis_hints)

        messages = [{"role": e["role"], "text": e["text"]} for e in self.history]
        messages.append({
            "role": "user",
            "text": f"위 답변을 바탕으로 다음 면접 질문을 해 주세요.\n\n현재 질문 순서: {self.question_count + 1}번째",
        })

        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=False)
        self.add_question(result["text"])
        return result

    async def _generate_closing(self) -> dict:
        messages = [{"role": e["role"], "text": e["text"]} for e in self.history]
        messages.append({
            "role": "user",
            "text": "모든 질문이 끝났습니다. 면접을 마무리하는 인사를 해 주세요. 수고했다는 격려와 함께 짧게 마무리합니다. next_question에 마무리 멘트를 작성하고, 새로운 질문은 하지 마세요.",
        })
        data = await call_gemini_json(self._build_system_prompt(), messages)
        result = self._json_to_result(data, finished=True)
        self.finished = True
        return result
