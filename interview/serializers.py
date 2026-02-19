from rest_framework import serializers


class NotifyRequestSerializer(serializers.Serializer):
    roomId = serializers.CharField(max_length=255)
