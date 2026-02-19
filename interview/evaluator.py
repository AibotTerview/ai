import logging
import uuid
from pathlib import Path

from google import genai
from django.conf import settings
from django.utils import timezone

from .models import InterviewQuestion, InterviewScore, Interview

logger = logging.getLogger(__name__)

_EVALUATOR_PROMPT_PATH = Path(__file__).resolve().parent / "evaluate" / "evaluator_prompt.txt"
_evaluator_prompt_template: str | None = None

def _get_evaluator_prompt_template() -> str:
    global _evaluator_prompt_template
    if _evaluator_prompt_template is None:
        with open(_EVALUATOR_PROMPT_PATH, encoding="utf-8") as f:
            _evaluator_prompt_template = f.read()
    return _evaluator_prompt_template

class InterviewEvaluator:
    _instance = None

    # In-memory context storage: { interview_id: [ (sequence, question, answer, evaluation), ... ] }
    _context_storage = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(InterviewEvaluator, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        self.client = genai.Client(api_key=api_key)

    def evaluate(self, interview_id: str, sequence: int, question: str, answer: str):
        if interview_id not in self._context_storage:
            self._context_storage[interview_id] = []

        current_entry = {
            'sequence': sequence,
            'question': question,
            'answer': answer,
            'evaluation': "평가 진행 중..."
        }
        self._context_storage[interview_id].append(current_entry)

        history = [entry for entry in self._context_storage.get(interview_id, []) if entry['sequence'] < sequence]

        prompt = self._construct_prompt(history, question, answer)

        response = self.client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        evaluation = response.text
        current_entry['evaluation'] = evaluation
        self._save_to_db(interview_id, sequence, question, answer, evaluation)

    def _construct_prompt(self, history, current_question, current_answer):
        instruction = _get_evaluator_prompt_template()

        if history:
            context_lines = ["--- Previous Conversation (Context) ---"]
            for entry in history:
                context_lines.append(f"Q: {entry['question']}\nA: {entry['answer']}")
            context = "\n\n".join(context_lines) + "\n\n"
        else:
            context = ""

        prompt = instruction + "\n\n" + context
        prompt += f"--- Current Question (Criteria) ---\n{current_question}\n\n"
        prompt += f"--- Candidate's Answer (Target) ---\n{current_answer}\n\n"
        prompt += "Evaluation (in Korean):"
        return prompt

    def _save_to_db(self, interview_id, question, answer, evaluation):
        interview = Interview.objects.get(interview_id=interview_id)

        InterviewQuestion.objects.create(
            question_id=str(uuid.uuid4()),
            interview=interview,
            question=question,
            answer=answer,
            feedback=evaluation,
            created_at=timezone.now(),
        )

    def get_context(self, interview_id: str):
        return self._context_storage.get(interview_id, [])
