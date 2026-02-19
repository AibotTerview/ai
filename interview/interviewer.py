import json
import asyncio
from typing import List, Dict
from google import genai
from google.genai import types
from django.conf import settings
from asgiref.sync import sync_to_async

from interview.personas import PersonaService
from interview.context import LLMContextService
from interview.schemas import LLM_RESPONSE_JSON_SCHEMA


class GeminiClient:
    _client: genai.Client | None = None

    @classmethod
    def _get_client(cls) -> genai.Client:
        if cls._client is None:
            cls._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return cls._client

    @staticmethod
    def _build_contents(messages: List[Dict[str, str]]) -> List[types.Content]:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["text"])]))
        return contents

    @classmethod
    def _call_gemini_json_sync(cls, system_prompt: str, messages: List[Dict[str, str]]) -> Dict:
        client = cls._get_client()
        contents = cls._build_contents(messages)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=LLM_RESPONSE_JSON_SCHEMA,
            ),
        )
        return json.loads(response.text)


async def call_gemini_json(system_prompt: str, messages: List[Dict[str, str]]) -> Dict:
    loop = asyncio.get_event_loop()
    
    def sync_call():
        return GeminiClient._call_gemini_json_sync(system_prompt, messages)
    
    return await loop.run_in_executor(None, sync_call)


class InterviewSession:
    MAX_FOLLOWUPS_PER_QUESTION = 3

    def __init__(self, persona: str = "FORMAL", max_questions: int = 5, setting_id: str | None = None) -> None:
        self.persona = persona.upper()
        self.max_questions = max_questions
        self.setting_id = setting_id
        self.question_count = 0
        self.followup_count = 0       # 현재 질문에 대한 꼬리질문 누적 횟수
        self.history: List[Dict[str, str]] = []
        self.finished = False
        self._setting_context: str = ""

    async def async_setup(self) -> None:
        self._setting_context = await sync_to_async(LLMContextService.get_setting_context)(self.setting_id)

    def add_question(self, text: str) -> None:
        self.history.append({"role": "interviewer", "text": text})
        self.question_count += 1

    def add_answer(self, text: str) -> None:
        self.history.append({"role": "user", "text": text})

    def is_last_question(self) -> bool:
        return self.question_count >= self.max_questions

    def _build_system_prompt(self) -> str:
        persona_text = PersonaService.get_persona(self.persona)
        followup_remaining = self.MAX_FOLLOWUPS_PER_QUESTION - self.followup_count
        return (
            f"{persona_text}\n\n"
            "면접 진행 규칙:\n"
            "- 한 번에 하나의 질문만 합니다.\n"
            "- 질문은 간결하게 1~3문장으로 합니다.\n"
            f"- 현재 {self.question_count}/{self.max_questions}번째 질문입니다.\n"
            f"- 현재 질문에 대한 꼬리질문 가능 횟수: {followup_remaining}회\n"
            "- 꼬리질문 규칙:\n"
            "  - 지원자의 답변이 모호하거나 구체성이 부족하면 is_followup=true로 꼬리질문을 합니다.\n"
            "  - 꼬리질문 가능 횟수가 0이면 반드시 is_followup=false로 새 주제 질문을 합니다.\n"
            "  - 답변이 충분히 구체적이면 is_followup=false로 새 주제 질문을 합니다.\n\n"
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

    def _can_followup(self) -> bool:
        """꼬리질문 가능 여부: 꼬리질문 횟수 제한 & 전체 질문 한계치 미달"""
        followup_not_exhausted = self.followup_count < self.MAX_FOLLOWUPS_PER_QUESTION
        total_not_exceeded = self.question_count < self.max_questions
        return followup_not_exhausted and total_not_exceeded

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
        if analysis_hints:
            system_prompt += "\n\n분석 참고:\n" + "\n".join(analysis_hints)

        # 히스토리를 메시지 형식으로 변환
        messages: List[Dict[str, str]] = []
        for entry in self.history:
            messages.append({"role": entry["role"], "text": entry["text"]})

        # 꼬리질문 가능 여부를 프롬프트에 명시
        if self._can_followup():
            next_instruction = (
                f"위 답변을 바탕으로 꼬리질문이 필요하면 is_followup=true, "
                f"새 주제면 is_followup=false로 다음 질문을 해 주세요.\n\n"
                f"현재 질문 순서: {self.question_count + 1}번째"
            )
        else:
            next_instruction = (
                f"꼬리질문 없이 새로운 주제로 다음 면접 질문을 해 주세요. is_followup=false.\n\n"
                f"현재 질문 순서: {self.question_count + 1}번째"
            )

        messages.append({"role": "user", "text": next_instruction})

        data = await call_gemini_json(system_prompt, messages)

        is_followup = data.get("is_followup", False) and self._can_followup()

        result = self._json_to_result(data, finished=False)

        if is_followup:
            # 꼬리질문: question_count도 증가, followup_count도 증가
            self.followup_count += 1
            result["text"] = f"[꼬리질문] {result['text']}"
            self.history.append({"role": "interviewer", "text": result["text"]})
            self.question_count += 1
        else:
            # 새 주제 질문: question_count 증가, followup_count 리셋
            self.followup_count = 0
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
