from django.apps import AppConfig


class AiBotConfig(AppConfig):
    name = 'ai_bot'

    def ready(self):
        import ai_bot.receivers
