import threading
import logging
from django.dispatch import receiver
from .signals import answer_submitted
from .evaluator import InterviewEvaluator

logger = logging.getLogger(__name__)

@receiver(answer_submitted)
def handle_answer_submission(sender, **kwargs):
    """
    Handles the answer_submitted signal asynchronously using a thread.
    Required kwargs: interview_id, sequence, question, answer
    """
    interview_id = kwargs.get('interview_id')
    sequence = kwargs.get('sequence')
    question = kwargs.get('question')
    answer = kwargs.get('answer')

    if not all([interview_id, question, answer]):
        logger.warning("[Signal] Missing arguments for answer_submitted signal.")
        return

    logger.info(f"[Signal] Received answer for {interview_id}. Starting async evaluation...")

    # Run evaluation in a separate thread to avoid blocking the main thread
    evaluator_thread = threading.Thread(
        target=_run_async_evaluation,
        args=(interview_id, sequence, question, answer),
        daemon=True
    )
    evaluator_thread.start()

def _run_async_evaluation(interview_id, sequence, question, answer):
    try:
        evaluator = InterviewEvaluator()
        evaluator.evaluate(interview_id, sequence, question, answer)
    except Exception as e:
        logger.error(f"[Signal] Async evaluation failed: {e}")
