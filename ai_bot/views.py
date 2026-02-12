import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import status

from ai_bot import signaling
from ai_bot.serializers import NotifyRequestSerializer


@csrf_exempt
@require_http_methods(["POST"])
def notify(request):
    body = json.loads(request.META['wsgi.input'].read().decode('utf-8'))
    serializer = NotifyRequestSerializer(data=body)

    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    room_id = serializer.validated_data["roomId"]
    started = signaling.start(room_id)
    if not started:
        return JsonResponse(
            {"error": "서버가 가득 찼습니다. 잠시 후 다시 시도해 주세요."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JsonResponse({"status": "ok"})
