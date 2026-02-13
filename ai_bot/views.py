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
    try:
        body = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)

    serializer = NotifyRequestSerializer(data=body)

    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    room_id = serializer.validated_data["roomId"]
    setting_id = serializer.validated_data.get("settingId")
    
    if not setting_id:
        setting_id = room_id
        
    if setting_id:
        try:
            from .models import Interview, InterviewSetting
            from django.utils import timezone
            
            setting = InterviewSetting.objects.get(setting_id=setting_id)
            
            interview, created = Interview.objects.get_or_create(
                interview_id=room_id,
                defaults={
                    'setting': setting,
                    'created_at': timezone.now()
                }
            )
            if created:
                print(f"[View] Created Interview: {room_id}")
                
        except InterviewSetting.DoesNotExist:
            return JsonResponse({"error": "Invalid settingId"}, status=400)
        except Exception as e:
            print(f"[View] Error creating interview: {e}")
            return JsonResponse({"error": f"Failed to create interview: {str(e)}"}, status=500)

    # Signaling 시작 (세션 제한 체크)
    started = signaling.start(room_id)
    if not started:
        return JsonResponse(
            {"error": "서버가 가득 찼습니다. 잠시 후 다시 시도해 주세요."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return JsonResponse({"status": "ok"})
