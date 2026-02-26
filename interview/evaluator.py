import json
import logging
import uuid
from google import genai
from google.genai import types as genai_types
from django.conf import settings
from django.utils import timezone
from .models import InterviewQuestion, InterviewScore, Interview

SCORE_TYPES = ['OVERALL', 'RESPONSE_ACCURACY', 'SPEAKING_PACE', 'VOCABULARY_QUALITY', 'PRONUNCIATION_ACCURACY']

_SCORE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "evaluation": {"type": "string"},
    },
    "required": ["score", "evaluation"],
}

OVERALL_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_review": {"type": "string"},
        "scores": {
            "type": "object",
            "properties": {st: _SCORE_ITEM_SCHEMA for st in SCORE_TYPES},
            "required": SCORE_TYPES,
        },
    },
    "required": ["overall_review", "scores"],
}

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
                sequence=sequence,
                elapsed_time=0,
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
        면접 종료 시 전체 Q&A + 개별 피드백을 종합해 최종 AI 평가 및 점수를 생성하고 DB에 저장.
        duration: 면접 진행 시간 (초 단위) — DB에는 밀리초로 변환해 저장
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
            logger.warning(f"[Evaluator] No questions for {interview_id}. Saving duration only.")
            interview.duration = duration * 1000
            interview.save(update_fields=['duration'])
            return

        prompt = self._construct_overall_prompt(questions)

        result = None
        try:
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=OVERALL_REVIEW_SCHEMA,
                ),
            )
            result = json.loads(response.text)
            logger.info(f"[Evaluator] Overall review generated for {interview_id}")
        except Exception as e:
            logger.error(f"[Evaluator] Overall review API call failed: {e}")

        try:
            interview.duration = duration * 1000  # 초 → 밀리초
            interview.ai_overall_review = (result or {}).get("overall_review", "AI 전체 평가를 생성하지 못했습니다.")
            interview.save(update_fields=['duration', 'ai_overall_review'])

            if result:
                scores_data = result.get("scores", {})
                for score_type in SCORE_TYPES:
                    score_item = scores_data.get(score_type, {})
                    score_val = score_item.get("score")
                    if score_val is not None:
                        InterviewScore.objects.create(
                            score_id=str(uuid.uuid4()),
                            interview=interview,
                            score_type=score_type,
                            score=int(score_val),
                            evaludation=score_item.get("evaluation", ""),
                        )

            logger.info(f"[Evaluator] Overall review & scores saved for {interview_id} (duration={duration}s)")
        except Exception as e:
            logger.error(f"[Evaluator] Overall review DB save failed: {e}")

    def _construct_overall_prompt(self, questions) -> str:
        prompt = (
            "You are an expert technical interviewer and evaluator.\n"
            "Below is the complete record of a job interview, including each question, "
            "the candidate's answer, and the immediate AI feedback given at the time.\n"
            "Based on ALL of this information, produce a JSON response with:\n\n"
            "1. overall_review: 종합 최종 평가 텍스트 (한국어, 500자 이내)\n"
            "   - 전반적인 답변 품질, 기술적 역량, 강점, 개선점을 포함\n\n"
            "2. scores: 아래 5개 항목에 대해 각각 0~100 정수 점수와 한국어 평가 한 줄 작성\n"
            "   - OVERALL: 전체 종합 점수\n"
            "   - RESPONSE_ACCURACY: 질문에 대한 답변의 정확성·관련성\n"
            "   - SPEAKING_PACE: 답변의 명료함·간결함 (음성 속도 대신 텍스트 답변 기준)\n"
            "   - VOCABULARY_QUALITY: 어휘 수준 및 전문 용어 활용도\n"
            "   - PRONUNCIATION_ACCURACY: 답변의 표현 정확성 및 일관성\n\n"
            "IMPORTANT: All text fields must be in Korean (한국어).\n\n"
            "--- Interview Record ---\n\n"
        )

        for idx, q in enumerate(questions, start=1):
            prompt += f"[{idx}번 문항]\n"
            prompt += f"질문: {q.question}\n"
            prompt += f"답변: {q.answer or '(답변 없음)'}\n"
            prompt += f"개별 피드백: {q.feedback or '(피드백 없음)'}\n\n"

        return prompt
