import os
import django
import time
import threading
from django.conf import settings
from django.utils import timezone

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ai.settings')
django.setup()

from ai_bot.signals import answer_submitted
from ai_bot.models import Interview, InterviewQuestion, InterviewScore, InterviewSetting, User
from ai_bot.evaluator import InterviewEvaluator

def run_test():
    print("[Test] Starting Event-Driven AI Evaluator Test...")
    
    # Initialize Evaluator (Singleton)
    evaluator = InterviewEvaluator()

    # 1. Create Dummy Data
    try:
        # Create User if not exists
        user, _ = User.objects.get_or_create(
            user_id='test-user-001',
            defaults={
                'email': 'test@example.com',
                'password': 'hashed_password',
                'name': 'Test User',
                'created_at': timezone.now()
            }
        )

        # Create InterviewSetting
        setting, _ = InterviewSetting.objects.get_or_create(
            setting_id='test-setting-001',
            defaults={
                'user': user,
                'question_count': 3,
                'interviewer_style': 'polite',
                'interviewer_gender': 'male',
                'interviewer_appearance': 'formal',
                'created_at': timezone.now()
            }
        )

        # Create Interview
        interview_id = 'test-interview-001'
        Interview.objects.filter(interview_id=interview_id).delete() # Clean up previous test
        

        now = timezone.now()
        
        interview = Interview.objects.create(
            interview_id=interview_id,
            setting=setting,
            created_at=now
        )
        print(f"[Test] Created Interview: {interview_id}")

    except Exception as e:
        print(f"[Test] Failed to create dummy data: {e}")
        return

    # 2. Simulate Q&A Sequence
    questions = [
        (1, "자기소개를 해주세요.", "저는 성실하고 열정적인 개발자입니다."),
        (2, "가장 도전적이었던 경험은 무엇인가요?", "프로젝트 기한을 맞추기 위해 팀원들과 밤샘 작업을 하며 협업한 경험이 있습니다."),
        (3, "마지막으로 하고 싶은 말은?", "뽑아주시면 열심히 하겠습니다.")
    ]

    for seq, q, a in questions:
        print(f"\n[Test] Sending Answer Signal for Sequence {seq}...")
        start_time = time.time()
        
        # Fire Signal
        answer_submitted.send(
            sender=None,
            interview_id=interview_id,
            sequence=seq,
            question=q,
            answer=a
        )
        
        elapsed = time.time() - start_time
        print(f"[Test] Signal sent in {elapsed:.4f}s. Main thread is free.")
        print(f"[Test] dictionary: {evaluator.get_context(interview_id)}")
        
        # In a real app, we wouldn't wait here. But for testing, we pause to simulate intervals.
        # time.sleep(1)  # Commented out to test race condition / rapid requests 

    print("\n[Test] Waiting for async tasks to complete (approx 10s)...")
    time.sleep(10)

    # 3. Verify Results in DB
    print("\n[Test] Verifying DB Results...")
    questions_db = InterviewQuestion.objects.filter(interview=interview)

    print(f"[Test] Questions saved: {questions_db.count()} (Expected 3)")
    
    for q_obj in questions_db:
        print(f"[Test] Question: {q_obj.question}")
        # feedback은 DB 컬럼 없음 — Evaluator._context_storage에만 유지

if __name__ == "__main__":
    run_test()
