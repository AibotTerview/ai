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
        }
    },
    "required": ["sequence", "next_question", "face"]
}
