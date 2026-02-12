import os
import json
from datetime import datetime
from typing import Optional, Dict, List, Any
from dotenv import load_dotenv
import google.generativeai as genai
from ai_bot.models import InterviewSetting
from step2.utils.personas import PersonaService
from step2.utils.context import LLMContextService
from step2.schemas import (
    LLM_RESPONSE_JSON_SCHEMA,
    QuestionResponse,
    JsonSession,
)

load_dotenv()
API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=API_KEY)

MAX_SESSIONS = 100


class LLMService:
    def __init__(self):
        self.context_service = LLMContextService()
        self.persona_service = PersonaService()
        self._json_sessions: Dict[str, JsonSession] = {}
        self._prompt_logs: List[Dict[str, Any]] = []

    def _get_system_instruction(self, setting_id: str, persona_name: Optional[str] = None) -> str:
        base = self.persona_service.get_persona(persona_name)
        setting_context = self.context_service.get_setting_context(setting_id)
        if setting_context:
            return base + "\n\n[면접 설정 정보 (아래 내용을 참고해서 질문을 생성해)]\n" + setting_context
        return base

    def _get_or_create_json_session(self, setting_id: str) -> JsonSession:
        if setting_id not in self._json_sessions:
            if len(self._json_sessions) >= MAX_SESSIONS:
                oldest_key = min(
                    self._json_sessions,
                    key=lambda k: self._json_sessions[k]["created_at"],
                )
                del self._json_sessions[oldest_key]

            self._json_sessions[setting_id] = {
                "setting_id": setting_id,
                "messages": [],
                "turn_count": 0,
                "created_at": datetime.now().isoformat(),
            }
        return self._json_sessions[setting_id]

    def _get_json_history_for_api(self, setting_id: str) -> List[Dict[str, str]]:
        session = self._get_or_create_json_session(setting_id)
        history = []
        for msg in session["messages"]:
            api_role = "model" if msg["role"] == "assistant" else "user"
            content = msg["content"]
            if msg.get("json_response"):
                content = f"{content}\n\nJSON: {json.dumps(msg['json_response'], ensure_ascii=False)}"
            history.append({"role": api_role, "parts": [content]})
        return history

    def generate_question(
        self,
        setting_id: str,
        user_answer: Optional[str] = None,
    ) -> QuestionResponse:
        setting = InterviewSetting.objects.get(setting_id=setting_id)
        session = self._get_or_create_json_session(setting_id)
        sequence = session["turn_count"] // 2 + 1

        is_closing = sequence > setting.question_count

        if is_closing and not user_answer:
            raise ValueError(
                f"질문 개수 초과: {sequence}/{setting.question_count}"
            )

        if is_closing and user_answer:
            prompt = (
                f"사용자의 마지막 답변: {user_answer}\n\n"
                "이것이 마지막 답변입니다. 위 답변에 대한 반응과 함께 면접 마무리 인사를 해주세요. "
                "next_question에 마무리 멘트를 작성하고, 새로운 질문은 하지 마세요."
            )
            prompt_with_sequence = f"{prompt}\n\n면접 종료"
        elif user_answer:
            prompt = (
                f"사용자의 이전 답변: {user_answer}\n\n"
                "위 답변의 품질, 성실도, 태도를 평가한 뒤 그에 맞는 표정(face)을 선택하고, "
                "next_question에 이전 답변에 대한 반응과 다음 질문을 함께 작성해주세요. "
                "답변이 불성실하거나 부적절하면 바로 다음 주제로 넘어가지 말고 대응해주세요."
            )
            prompt_with_sequence = f"{prompt}\n\n현재 질문 순서: {sequence}번째"
        else:
            prompt = "첫 번째 면접 질문을 생성해주세요."
            prompt_with_sequence = f"{prompt}\n\n현재 질문 순서: {sequence}번째"

        history = self._get_json_history_for_api(setting_id)

        persona_from_db = getattr(setting, "interviewer_style", None)
        system_instruction = self._get_system_instruction(setting_id, persona_from_db)
        use_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_instruction,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": LLM_RESPONSE_JSON_SCHEMA,
            },
        )

        self._prompt_logs.append(
            {
                "setting_id": setting_id,
                "sequence": sequence,
                "persona": persona_from_db,
                "system_instruction": system_instruction,
                "history": history,
                "prompt": prompt_with_sequence,
                "before_user_answer": user_answer,
                "timestamp": datetime.now().isoformat(),
            }
        )

        chat_session = use_model.start_chat(history=history)

        response = chat_session.send_message(prompt_with_sequence)
        json_text = response.text
        result = json.loads(json_text)

        result["sequence"] = sequence
        result["fin"] = is_closing
        if user_answer:
            result["before_user_answer"] = user_answer

        session = self._get_or_create_json_session(setting_id)
        session["messages"].append({
            "role": "user",
            "content": prompt_with_sequence,
            "json_response": None,
            "timestamp": datetime.now().isoformat(),
        })
        session["messages"].append({
            "role": "assistant",
            "content": json_text,
            "json_response": result,
            "timestamp": datetime.now().isoformat(),
        })
        session["turn_count"] = len(session["messages"])

        return result

    def get_prompt_logs(self) -> List[Dict[str, Any]]:
        return list(self._prompt_logs)

    def clear_prompt_logs(self) -> None:
        self._prompt_logs.clear()

    def get_session_info(self, setting_id: str) -> JsonSession:
        return self._get_or_create_json_session(setting_id)

    def clear_session(self, setting_id: str):
        if setting_id in self._json_sessions:
            del self._json_sessions[setting_id]

_llm_service_instance: Optional[LLMService] = None

def get_llm_service() -> LLMService:
    global _llm_service_instance
    if _llm_service_instance is None:
        _llm_service_instance = LLMService()
    return _llm_service_instance
