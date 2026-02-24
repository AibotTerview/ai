from django.dispatch import Signal

# Interview ID, Sequence, Question, Answer
answer_submitted = Signal()

# Interview ID, Duration (seconds)
interview_ended = Signal()
