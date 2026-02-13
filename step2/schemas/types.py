from typing import Optional, List, TypedDict


class QuestionResponse(TypedDict):
    sequence: int
    next_question: str
    face: str
    before_user_answer: Optional[str]
    fin: bool


class SessionMessage(TypedDict):
    role: str
    content: str
    json_response: Optional[QuestionResponse]
    timestamp: str


class JsonSession(TypedDict):
    setting_id: str
    messages: List[SessionMessage]
    turn_count: int
    created_at: str

