import os
import django
import uuid
from datetime import datetime
from django.utils import timezone

# Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ai.settings')
django.setup()

# Import models ONLY AFTER django.setup()
from ai_bot.models import Interview, InterviewSetting, InterviewMaterial, InterviewScore, InterviewQuestion, User
from ai_bot.db import save_interview_result

def verify_db_logic():
    # 1. Prepare Test Data
    user_id = str(uuid.uuid4())
    setting_id = str(uuid.uuid4())
    room_id = str(uuid.uuid4()) # interview_id
    
    dummy_video_url = "https://s3.ap-northeast-2.amazonaws.com/test-bucket/video.mp4"
    dummy_audio_url = "https://s3.ap-northeast-2.amazonaws.com/test-bucket/audio.wav"
    duration = 120.5

    print(f"--- [Test Start] Room ID: {room_id} ---")

    try:
        # ORM을 사용한 더미 데이터 생성
        now = timezone.now()

        # 1.1 Create dummy User (if needed)
        # Try to create a dummy user. If user_id is a FK, we need this.
        # Check if user exists or create one.
        try:
            user = User.objects.create(
                user_id=user_id,
                email="test@example.com",
                password="password",
                name="Test User",
                created_at=now
            )
        except Exception as e:
            # If user table issues (e.g. duplicate or whatever), try to fetch an existing one or ignore
            print(f"[Info] User creation failed (might exist or other issue): {e}")
            # If failed, we might not have a valid user instance for setting.
            # Let's hope logic proceeds or we catch it.
        
        # 1.2 Create dummy InterviewSetting
        # Need a User instance
        try:
            # If we need a user instance for the ForeignKey:
            # We already tried creating one. Let's use it.
            # If creation failed, we might need to mock or fetch.
            # For this script, simplicity: Assume we created it or use raw SQL if ORM is strict.
            # ORM is strict. We need a valid User object.
            
            # If User creation failed above, getting it might work if it was duplicate.
            if 'user' not in locals():
                 user = User.objects.first() # Get any user
            
            setting = InterviewSetting.objects.create(
                setting_id=setting_id,
                user=user, # Assign User instance
                question_count=5,
                interviewer_style='Friendly',
                interviewer_gender='Male',
                interviewer_appearance='Casual',
                created_at=now
            )

            # 1.3 Create dummy Interview
            Interview.objects.create(
                interview_id=room_id,
                setting=setting, # Assign Setting instance
                created_at=now,
                interview_name="Test Interview"
            )
            
            print("1. Prepared dummy data (User, Setting, Interview) using ORM.")

        except Exception as e:
            print(f"[Data Prep Error] {e}")
            return

        # 3. Execute save logic
        print("2. Calling save_interview_result()...")
        success = save_interview_result(room_id, dummy_video_url, dummy_audio_url, duration)
        
        if success:
            print("   -> Success!")
        else:
            print("   -> Failed!")
            return

        # 4. Verify Data using ORM
        print("\n3. Verifying inserted data:")
        
        try:
            interview = Interview.objects.get(interview_id=room_id)
            print(f"   [Interview] Duration: {interview.duration}, Review: {interview.ai_overall_review}")

            materials = InterviewMaterial.objects.filter(interview=interview)
            print(f"   [Material] Found {materials.count()} records:")
            for m in materials:
                print(f"     - {m.material_type}: {m.file_path}")

            scores = InterviewScore.objects.filter(interview=interview)
            if scores.exists():
                s = scores.first()
                print(f"   [Score] Score: {s.score}, Eval: {s.evaludation}")

            questions = InterviewQuestion.objects.filter(interview=interview)
            print(f"   [Question] Found {questions.count()} records:")
            for q in questions:
                print(f"     - Q: {q.question[:30]}..., A: {q.answer[:30]}...")
        
        except Exception as e:
            print(f"[Verification Error] {e}")

    except Exception as e:
        print(f"\n[Error] {e}")

if __name__ == "__main__":
    verify_db_logic()
