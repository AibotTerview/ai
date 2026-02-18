import json
import asyncio
from typing import List, Dict
import google.generativeai as genai
from django.conf import settings

from interview.personas import PersonaService
from interview.context import LLMContextService
from interview.schemas import LLM_RESPONSE_JSON_SCHEMA


class GeminiClient:
    _configured = False

    @classmethod
    def _ensure_configured(self):
        if not self._configured:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self._configured = True

    @staticmethod
    def _build_contents(messages: List[Dict[str, str]]) -> List[Dict]:
        contents = []
        for msg in messages:
            if msg["role"] == "user":
                role = "user"
            else:
                role = "model"
            contents.append({"role": role, "parts": [msg["text"]]})
        return contents

    @classmethod
    def _call_gemini_json_sync(self, system_prompt: str, messages: List[Dict[str, str]]) -> Dict:
        self._ensure_configured()

        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=system_prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=LLM_RESPONSE_JSON_SCHEMA,
            ),
        )
        
        contents = self._build_contents(messages)
        response = model.generate_content(contents)
        
        return json.loads(response.text)


async def call_gemini_json(system_prompt: str, messages: List[Dict[str, str]]) -> Dict:
    loop = asyncio.get_event_loop()
    
    def sync_call():
        return GeminiClient._call_gemini_json_sync(system_prompt, messages)
    
    return await loop.run_in_executor(None, sync_call)


class InterviewSession:
    def __init__(self, persona: str = "FORMAL", max_questions: int = 5, setting_id: str | None = None) -> None:
        self.persona = persona.upper()
        self.max_questions = max_questions
        self.setting_id = setting_id
        self.question_count = 0
        self.history: List[Dict[str, str]] = []
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
        return (
            f"{persona_text}\n\n"
            "면접 진행 규칙:\n"
            "- 한 번에 하나의 질문만 합니다.\n"
            "- 질문은 간결하게 1~3문장으로 합니다.\n"
            f"- 현재 {self.question_count}/{self.max_questions}번째 질문입니다.\n\n"
            "[면접 설정 정보 (아래 내용을 참고해서 질문을 생성해)]\n"
            f"{self._setting_context}"
        )

    @staticmethod
    def _json_to_result(data: Dict, finished: bool = False) -> Dict:
        return {
            "text": data["next_question"],
            "expression": data["face"],
            "finished": finished
        }

    async def generate_first_question(self) -> Dict:
        system_prompt = self._build_system_prompt()
        messages: List[Dict[str, str]] = [
            {"role": "user", "text": "첫 번째 면접 질문을 생성해주세요.\n\n현재 질문 순서: 1번째"}
        ]
        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=False)
        self.add_question(result["text"])
        return result

    async def process_answer(self, user_text: str) -> Dict:
        self.add_answer(user_text)

        if self.is_last_question():
            return await self._generate_closing()

        # 답변 길이 분석 힌트 생성
        analysis_hints: List[str] = []
        if len(user_text) < 20:
            analysis_hints.append("지원자의 답변이 매우 짧습니다. 더 구체적으로 답변하도록 유도해 주세요.")
        if len(user_text) > 500:
            analysis_hints.append("지원자의 답변이 길었습니다. 핵심을 요약하도록 유도할 수 있습니다.")

        # 시스템 프롬프트 구성
        system_prompt = self._build_system_prompt()
        if len(analysis_hints) > 0:
            hints_text = "\n".join(analysis_hints)
            system_prompt += "\n\n분석 참고:\n" + hints_text

        # 히스토리를 메시지 형식으로 변환
        messages: List[Dict[str, str]] = []
        for entry in self.history:
            messages.append({"role": entry["role"], "text": entry["text"]})
        
        # 다음 질문 요청 추가
        messages.append({
            "role": "user",
            "text": f"위 답변을 바탕으로 다음 면접 질문을 해 주세요.\n\n현재 질문 순서: {self.question_count + 1}번째",
        })

        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=False)
        self.add_question(result["text"])
        return result

    async def _generate_closing(self) -> Dict:
        # 히스토리를 메시지 형식으로 변환
        messages: List[Dict[str, str]] = []
        for entry in self.history:
            messages.append({"role": entry["role"], "text": entry["text"]})
        
        # 마무리 요청 추가
        messages.append({
            "role": "user",
            "text": "모든 질문이 끝났습니다. 면접을 마무리하는 인사를 해 주세요. 수고했다는 격려와 함께 짧게 마무리합니다. next_question에 마무리 멘트를 작성하고, 새로운 질문은 하지 마세요.",
        })
        
        system_prompt = self._build_system_prompt()
        data = await call_gemini_json(system_prompt, messages)
        result = self._json_to_result(data, finished=True)
        self.finished = True
        return result
