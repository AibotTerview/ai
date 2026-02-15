import logging
import uuid
from google import genai
from django.conf import settings
from django.utils import timezone
from .models import InterviewQuestion, InterviewScore, Interview

logger = logging.getLogger(__name__)

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
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            logger.warning("[Evaluator] GEMINI_API_KEY not found. AI features will be disabled.")
            self.client = None

    def evaluate(self, interview_id: str, sequence: int, question: str, answer: str):
        """
        Evaluates the answer using Gemini, considering the context of previous Q&A.
        """
        if not self.client:
            logger.error("[Evaluator] Client not initialized. Skipping evaluation.")
            return

        # 0. Initialize Context if needed
        if interview_id not in self._context_storage:
            self._context_storage[interview_id] = []

        # 1. Pre-save to Memory (Mark as Pending) to avoid Race Condition
        # We store a mutable dictionary so we can update it later.
        current_entry = {
            'sequence': sequence,
            'question': question,
            'answer': answer,
            'evaluation': "평가 진행 중..." # Pending status
        }
        self._context_storage[interview_id].append(current_entry)

        # 2. Retrieve Context (excluding current one for prompt construction)
        # We filter out the current sequence to avoid self-reference in prompt if needed,
        # but logically previous items are what matters.
        history = [entry for entry in self._context_storage.get(interview_id, []) if entry['sequence'] < sequence]

        # 3. Construct Prompt
        prompt = self._construct_prompt(history, question, answer)

        evaluation = "평가 실패 (API Error)"
        try:
            # 4. Call Gemini API
            # Trying 'gemini-2.5-flash' (Standard Free Tier model)
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            evaluation = response.text
            logger.info(f"[Evaluator] Evaluation sequence {sequence} for {interview_id} completed.")

        except Exception as e:
            logger.error(f"[Evaluator] API Call failed: {e}")
            evaluation = f"AI 평가를 불러올 수 없습니다. 원인: {str(e)}"

        # 5. Update Memory (In-Place Update)
        current_entry['evaluation'] = evaluation

        # 6. Save to DB - Always save Q&A
        self._save_to_db(interview_id, sequence, question, answer, evaluation)

    def _construct_prompt(self, history, current_question, current_answer):
        prompt = "You are an AI Interviewer Evaluator. Your task is to evaluate the candidate's answer.\n"
        prompt += "The 'Question' provided is the context or the problem given to the candidate.\n"
        prompt += "The 'Answer' is the candidate's response which you must evaluate.\n"
        prompt += "Do NOT evaluate the quality of the question itself. Focus ONLY on the quality of the answer in response to the question.\n\n"

        prompt += "1. Analyze the answer's content, clarity, and relevance.\n"
        prompt += "2. Check for CONSISTENCY with previous answers (Context). Point out any contradictions.\n"
        prompt += "IMPORTANT: You MUST provide the evaluation feedback entirely in Korean (한국어).\n\n"

        if history:
            prompt += "--- Previous Conversation (Context) ---\n"
            # We only provide the Q&A history to check consistency, NOT the previous evaluations.
            for entry in history:
                prompt += f"Q: {entry['question']}\nA: {entry['answer']}\n\n"

        prompt += f"--- Current Question (Criteria) ---\n{current_question}\n\n"
        prompt += f"--- Candidate's Answer (Target) ---\n{current_answer}\n\n"
        prompt += "Evaluation (in Korean):"
        return prompt

    def _save_to_db(self, interview_id, sequence, question, answer, evaluation):
        try:
            interview = Interview.objects.get(interview_id=interview_id)

            # Save Question & Answer & Feedback
            InterviewQuestion.objects.create(
                question_id=str(uuid.uuid4()),
                interview=interview,
                question=question,
                answer=answer,
                created_at=timezone.now(),
                feedback=evaluation
            )

            logger.info(f"[Evaluator] Saved result to DB for {interview_id}")

        except Interview.DoesNotExist:
            logger.error(f"[Evaluator] Interview {interview_id} not found.")
        except Exception as e:
            logger.error(f"[Evaluator] DB Save failed: {e}")
    def get_context(self, interview_id: str):
        """
        Returns the conversation history for a specific interview.
        This can be called from the main thread (Interviewer AI).
        """
        return self._context_storage.get(interview_id, [])
