import os
import django
import uuid
from datetime import datetime
from django.utils import timezone

# Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ai.settings')
django.setup()

from ai_bot.models import Interview, InterviewSetting, InterviewMaterial, InterviewScore, InterviewQuestion, User
from ai_bot.db import save_interview_result
from ai_bot.storage import upload_file_to_s3

def verify_s3_and_db():
    print("--- [Test Start] S3 Upload & DB Save Verification ---")

    # 1. Create Dummy Files
    dummy_video_file = "test_video.mp4"
    dummy_audio_file = "test_audio.wav"
    
    with open(dummy_video_file, "wb") as f:
        f.write(b"dummy video content")
    with open(dummy_audio_file, "wb") as f:
        f.write(b"dummy audio content")
    
    print("1. Created dummy files locally.")

    try:
        # 2. Upload to S3
        print("2. Uploading to S3...")
        video_url = upload_file_to_s3(dummy_video_file, "video/mp4")
        audio_url = upload_file_to_s3(dummy_audio_file, "audio/wav")

        if not video_url or not audio_url:
            print("[Error] S3 Upload failed. Check endpoints or credentials.")
            return

        print(f"   -> Video URL: {video_url}")
        print(f"   -> Audio URL: {audio_url}")

        # 3. Prepare DB Data (User, Setting, Interview)
        user_id = str(uuid.uuid4())
        setting_id = str(uuid.uuid4())
        room_id = str(uuid.uuid4())
        now = timezone.now()
        duration = 60.0

        print(f"3. Preparing DB data (Room ID: {room_id})...")
        
        # User & Setting (Assuming strict FK)
        try:
             # Try to create a dummy user. If fails (e.g. constraints), try fetching.
            try:
                user = User.objects.create(
                    user_id=user_id, email="s3test@example.com", password="pw", name="S3 Tester", created_at=now
                )
            except:
                user = User.objects.first()

            setting = InterviewSetting.objects.create(
                setting_id=setting_id, user=user, question_count=3, 
                interviewer_style='Strict', interviewer_gender='Female', interviewer_appearance='Suit', 
                created_at=now
            )
            Interview.objects.create(
                interview_id=room_id, setting=setting, created_at=now, interview_name="S3 Test Interview"
            )
        except Exception as e:
            print(f"[Data Prep Error] {e}")
            return

        # 4. Save Result to DB
        print("4. Saving result to DB...")
        success = save_interview_result(room_id, video_url, audio_url, duration)

        if success:
            print("   -> Success! DB transaction complete.")
            
            # 5. Verify Material Links in DB
            materials = InterviewMaterial.objects.filter(interview_id=room_id)
            print(f"   [Verification] Found {materials.count()} material records:")
            for m in materials:
                print(f"     - {m.material_type}: {m.file_path}")
                if m.file_path in [video_url, audio_url]:
                     print("       -> URL Matches!")
                else:
                     print("       -> URL Mismatch!")

        else:
            print("   -> Failed to save to DB.")

    except Exception as e:
        print(f"\n[Error] {e}")

    finally:
        # Cleanup local files
        if os.path.exists(dummy_video_file):
            os.remove(dummy_video_file)
        if os.path.exists(dummy_audio_file):
            os.remove(dummy_audio_file)
        print("\n--- [Test End] Cleared local dummy files. ---")

if __name__ == "__main__":
    verify_s3_and_db()
