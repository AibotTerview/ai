
from django.contrib import admin
from django.urls import path

from ai_bot.views import notify

urlpatterns = [
    path('admin/', admin.site.urls),
    path('notify/', notify, name='notify'),
]
