import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import Interview, InterviewSetting
from django.utils import timezone

from signaling.session import start as signaling_start
from .serializers import NotifyRequestSerializer

@csrf_exempt
@require_http_methods(["POST"])
def notify(request):

    body = json.loads(request.body.decode('utf-8'))
    serializer = NotifyRequestSerializer(data=body)

    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    room_id = serializer.validated_data["roomId"]
    setting_id = room_id

    setting = InterviewSetting.objects.get(setting_id=setting_id)

    from signaling.session import get_session, remove_session
    if get_session(room_id) is not None:
        remove_session(room_id)

    Interview.objects.get_or_create(
        interview_id=room_id,
        defaults={"setting": setting, "created_at": timezone.now()},
    )

    if signaling_start(room_id):
        return JsonResponse({"status": "ok"})

    return JsonResponse({"error": "서버 오류가 발생했습니다."}, status=500)
