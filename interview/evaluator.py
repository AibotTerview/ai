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

    def generate_overall_review(self, interview_id: str, duration: int):
        """
        면접 종료 시 전체 Q&A + 개별 피드백을 종합해 최종 AI 평가를 생성하고 DB에 저장.
        duration: 면접 진행 시간 (초 단위)
        """
        if not self.client:
            logger.error("[Evaluator] Client not initialized. Skipping overall review.")
            return

        try:
            interview = Interview.objects.get(interview_id=interview_id)
        except Interview.DoesNotExist:
            logger.error(f"[Evaluator] Interview {interview_id} not found for overall review.")
            return

        questions = InterviewQuestion.objects.filter(interview=interview).order_by('created_at')

        if not questions.exists():
            logger.warning(f"[Evaluator] No questions found for interview {interview_id}. Skipping overall review.")
            # duration만 저장
            interview.duration = duration
            interview.save(update_fields=['duration'])
            return

        prompt = self._construct_overall_prompt(questions)

        overall_review = "전체 평가 생성 실패 (API Error)"
        try:
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            overall_review = response.text
            logger.info(f"[Evaluator] Overall review generated for {interview_id}")
        except Exception as e:
            logger.error(f"[Evaluator] Overall review API call failed: {e}")
            overall_review = f"AI 전체 평가를 불러올 수 없습니다. 원인: {str(e)}"

        try:
            interview.duration = duration
            interview.ai_overall_review = overall_review
            interview.save(update_fields=['duration', 'ai_overall_review'])
            logger.info(f"[Evaluator] Overall review saved for {interview_id} (duration={duration}s)")
        except Exception as e:
            logger.error(f"[Evaluator] Overall review DB save failed: {e}")

    def _construct_overall_prompt(self, questions) -> str:
        prompt = (
            "You are an expert technical interviewer and evaluator.\n"
            "Below is the complete record of a job interview, including each question, "
            "the candidate's answer, and the immediate AI feedback given at the time.\n"
            "Based on ALL of this information, write a comprehensive final evaluation of the candidate.\n\n"
            "Your evaluation should cover:\n"
            "1. 전반적인 답변 품질 (논리성, 구체성, 명확성)\n"
            "2. 기술적 역량 및 지식 수준\n"
            "3. 일관성 및 태도\n"
            "4. 강점과 개선이 필요한 점\n"
            "5. 종합 의견\n\n"
            "IMPORTANT: Write the entire evaluation in Korean (한국어).\n\n"
            "--- Interview Record ---\n\n"
        )

        for idx, q in enumerate(questions, start=1):
            prompt += f"[{idx}번 문항]\n"
            prompt += f"질문: {q.question}\n"
            prompt += f"답변: {q.answer or '(답변 없음)'}\n"
            prompt += f"개별 피드백: {q.feedback or '(피드백 없음)'}\n\n"

        prompt += "--- 종합 최종 평가 (한국어로 작성) ---\n"
        return prompt
