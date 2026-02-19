import logging
import uuid
from google import genai
from django.conf import settings
from django.utils import timezone
from .models import InterviewQuestion, InterviewScore, Interview

logger = logging.getLogger(__name__)

class InterviewEvaluator:
    _instance = None

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

    def evaluate(self, interview_id: str, sequence: int, question: str, answer: str, history: list = []):
        """
        Evaluates the answer using Gemini, considering the context of previous Q&A.
        history: snapshot of InterviewSession.history at the time of answer submission (thread-safe copy).
        """
        if not self.client:
            logger.error("[Evaluator] Client not initialized. Skipping evaluation.")
            return

        # 현재 답변 이전의 Q&A만 context로 사용 (interviewer 역할 항목을 question으로, user 역할 항목을 answer로 매핑)
        prior_qa = self._extract_prior_qa(history, question)

        # Construct prompt using prior Q&A context
        prompt = self._construct_prompt(prior_qa, question, answer)

        evaluation = "평가 실패 (API Error)"
        try:
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            evaluation = response.text
            logger.info(f"[Evaluator] Evaluation sequence {sequence} for {interview_id} completed.")

        except Exception as e:
            logger.error(f"[Evaluator] API Call failed: {e}")
            evaluation = f"AI 평가를 불러올 수 없습니다. 원인: {str(e)}"

        # Save to DB
        self._save_to_db(interview_id, sequence, question, answer, evaluation)

    def _extract_prior_qa(self, history: list, current_question: str) -> list:
        """
        InterviewSession.history의 스냅샷에서 이전 Q&A 쌍을 추출.
        현재 질문 직전까지의 (question, answer) 쌍을 리스트로 반환.
        """
        prior_qa = []
        i = 0
        while i < len(history) - 1:
            entry = history[i]
            next_entry = history[i + 1]
            if entry.get("role") == "interviewer" and next_entry.get("role") == "user":
                q_text = entry.get("text", "")
                a_text = next_entry.get("text", "")
                # 현재 평가 대상 질문은 제외
                if q_text != current_question:
                    prior_qa.append({"question": q_text, "answer": a_text})
                i += 2
            else:
                i += 1
        return prior_qa

    def _construct_prompt(self, prior_qa: list, current_question: str, current_answer: str) -> str:
        prompt = "You are an AI Interviewer Evaluator. Your task is to evaluate the candidate's answer.\n"
        prompt += "The 'Question' provided is the context or the problem given to the candidate.\n"
        prompt += "The 'Answer' is the candidate's response which you must evaluate.\n"
        prompt += "Do NOT evaluate the quality of the question itself. Focus ONLY on the quality of the answer in response to the question.\n\n"

        prompt += "1. Analyze the answer's content, clarity, and relevance.\n"
        prompt += "2. Check for CONSISTENCY with previous answers (Context). Point out any contradictions.\n"
        prompt += "IMPORTANT: You MUST provide the evaluation feedback entirely in Korean (한국어).\n\n"

        if prior_qa:
            prompt += "--- Previous Conversation (Context) ---\n"
            for entry in prior_qa:
                prompt += f"Q: {entry['question']}\nA: {entry['answer']}\n\n"

        prompt += f"--- Current Question (Criteria) ---\n{current_question}\n\n"
        prompt += f"--- Candidate's Answer (Target) ---\n{current_answer}\n\n"
        prompt += "Evaluation (in Korean):"
        return prompt

    def _save_to_db(self, interview_id, sequence, question, answer, evaluation):
        try:
            interview = Interview.objects.get(interview_id=interview_id)

            InterviewQuestion.objects.create(
                question_id=str(uuid.uuid4()),
                interview=interview,
                question=question,
                answer=answer,
                feedback=evaluation,
                created_at=timezone.now(),
            )

            logger.info(f"[Evaluator] Saved result to DB for {interview_id}")

        except Interview.DoesNotExist:
            logger.error(f"[Evaluator] Interview {interview_id} not found.")
        except Exception as e:
            logger.error(f"[Evaluator] DB Save failed: {e}")
