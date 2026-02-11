import logging
import uuid
from datetime import datetime
from django.utils import timezone
from .models import Interview, InterviewMaterial, InterviewScore, InterviewQuestion

logger = logging.getLogger(__name__)

def save_interview_result(room_id: str, video_url: str, audio_url: str, duration: float) -> bool:
    """
    인터뷰 결과를 Django ORM을 사용하여 DB에 저장합니다.
    """
    
    # Placeholder / Dummy Data
    dummy_summary = "전반적으로 우수한 면접이었습니다. (AI 요약)"
    dummy_stt = "안녕하세요. 저는 이 프로젝트에 지원한 지원자입니다. 성실하게 답변하겠습니다."
    dummy_evaluation = "목소리가 또렷하고 자신감이 넘칩니다. (AI 평가 상세)"
    dummy_score = 80
    
    try:
        # 1. Update Interview
        updated_count = Interview.objects.filter(interview_id=room_id).update(
            duration=int(duration), 
            ai_overall_review=dummy_summary
        )
        
        if updated_count == 0:
            logger.warning(f"[DB] Interview not found for id {room_id}. Skipping details.")
            return False

        # Interview 인스턴스 가져오기
        interview_instance = Interview.objects.get(interview_id=room_id)
        now = timezone.now()

        # 2. Insert Video Material
        InterviewMaterial.objects.create(
            material_id=str(uuid.uuid4()),
            interview=interview_instance,
            material_type='VIDEO',
            file_path=video_url,
            created_at=now
        )

        # 2. Insert Audio Material
        InterviewMaterial.objects.create(
            material_id=str(uuid.uuid4()),
            interview=interview_instance,
            material_type='AUDIO',
            file_path=audio_url,
            created_at=now
        )

        # 3. Insert Score (evaludation typo check)
        InterviewScore.objects.create(
            score_id=str(uuid.uuid4()),
            interview=interview_instance,
            score_type='AI_EVAL',
            score=dummy_score,
            evaludation=dummy_evaluation
        )

        # 4. Insert Question (Dummy STT)
        InterviewQuestion.objects.create(
            question_id=str(uuid.uuid4()),
            interview=interview_instance,
            question="AI 면접 질문 (전체)",
            answer=dummy_stt,
            created_at=now,
            elapsed_time=int(duration)
        )

        logger.info(f"[DB] Saved full result for interview {room_id} (ORM)")
        return True

    except Exception as e:
        logger.error(f"[DB] Insert failed: {e}")
        return False
