"""LLM JSON 응답 스키마 (Gemini response_schema용)."""

LLM_RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "sequence": {
            "type": "integer",
            "description": "질문 순서"
        },
        "next_question": {
            "type": "string",
            "description": "이전 답변에 대한 피드백/반응과 다음 면접 질문을 합쳐서 작성"
        },
        "face": {
            "type": "string",
            "description": "3D 캐릭터 표정",
            "enum": ["happy", "neutral", "thinking", "serious", "smile", "curious", "encouraging"]
        },
        "before_user_answer": {
            "type": "string",
            "description": "유저의 이전 답변 (그대로 복사)",
            "nullable": True
        },
        "is_followup": {
            "type": "boolean",
            "description": "이전 답변이 모호하거나 구체성이 부족해 꼬리질문이 필요하면 True, 새 주제 질문이면 False"
        }
    },
    "required": ["sequence", "next_question", "face", "is_followup"]
}
