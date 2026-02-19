import threading
import logging
from django.dispatch import receiver
from .signals import answer_submitted
from .evaluator import InterviewEvaluator
from speech.audio_features import extract_features

logger = logging.getLogger(__name__)


@receiver(answer_submitted)
def handle_answer_submission(sender, **kwargs):
    interview_id = kwargs.get("interview_id")
    sequence = kwargs.get("sequence")
    question = kwargs.get("question")
    answer = kwargs.get("answer")
    wav_bytes = kwargs.get("wav_bytes")

    audio_features = extract_features(wav_bytes)

    evaluator_thread = threading.Thread(
        target=_run_async_evaluation,
        args=(interview_id, sequence, question, answer),
        kwargs={"audio_features": audio_features},
        daemon=True,
    )
    evaluator_thread.start()

def _run_async_evaluation(interview_id, sequence, question, answer, audio_features):
    evaluator = InterviewEvaluator()
    evaluator.evaluate(interview_id, sequence, question, answer, audio_features=audio_features)
