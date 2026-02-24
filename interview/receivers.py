import threading
import logging
from django.dispatch import receiver
from .signals import answer_submitted, interview_ended
from .evaluator import InterviewEvaluator

logger = logging.getLogger(__name__)

@receiver(answer_submitted)
def handle_answer_submission(sender, **kwargs):
    """
    Handles the answer_submitted signal asynchronously using a thread.
    Required kwargs: interview_id, sequence, question, answer, history
    """
    interview_id = kwargs.get('interview_id')
    sequence = kwargs.get('sequence')
    question = kwargs.get('question')
    answer = kwargs.get('answer')
    history = kwargs.get('history', [])

    if not all([interview_id, question, answer]):
        logger.warning("[Signal] Missing arguments for answer_submitted signal.")
        return

    logger.info(f"[Signal] Received answer for {interview_id}. Starting async evaluation...")

    # Run evaluation in a separate thread to avoid blocking the main thread
    evaluator_thread = threading.Thread(
        target=_run_async_evaluation,
        args=(interview_id, sequence, question, answer, history),
        daemon=True
    )
    evaluator_thread.start()

def _run_async_evaluation(interview_id, sequence, question, answer, history):
    try:
        evaluator = InterviewEvaluator()
        evaluator.evaluate(interview_id, sequence, question, answer, history)
    except Exception as e:
        logger.error(f"[Signal] Async evaluation failed: {e}")


@receiver(interview_ended)
def handle_interview_ended(sender, **kwargs):
    """
    Handles the interview_ended signal asynchronously using a thread.
    Required kwargs: interview_id, duration (seconds)
    """
    interview_id = kwargs.get('interview_id')
    duration = kwargs.get('duration', 0)

    if not interview_id:
        logger.warning("[Signal] Missing interview_id for interview_ended signal.")
        return

    logger.info(f"[Signal] Interview ended for {interview_id} (duration={duration}s). Starting overall review...")

    review_thread = threading.Thread(
        target=_run_overall_review,
        args=(interview_id, duration),
        daemon=True
    )
    review_thread.start()

def _run_overall_review(interview_id, duration):
    try:
        evaluator = InterviewEvaluator()
        evaluator.generate_overall_review(interview_id, duration)
    except Exception as e:
        logger.error(f"[Signal] Overall review generation failed: {e}")
