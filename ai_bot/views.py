import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import Interview, InterviewSetting
from django.utils import timezone

from ai_bot import signaling
from ai_bot.serializers import NotifyRequestSerializer

@csrf_exempt
@require_http_methods(["POST"])
def notify(request):

    body = json.loads(request.body.decode('utf-8'))
    serializer = NotifyRequestSerializer(data=body)

    room_id = serializer.validated_data["roomId"]
    setting_id = room_id

    setting = InterviewSetting.objects.get(setting_id=setting_id)

    Interview.objects.create(
        interview_id=room_id,
        setting=setting,
        created_at=timezone.now(),
    )

    signaling.start(room_id) # 시작
    return JsonResponse({"status": "ok"})
