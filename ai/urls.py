
from django.contrib import admin
from django.urls import path

from interview.views import notify

urlpatterns = [
    path('admin/', admin.site.urls),
    path('notify/', notify, name='notify'),
]
