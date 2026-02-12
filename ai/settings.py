import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

import pymysql
pymysql.install_as_MySQLdb()
pymysql.version_info = (2, 2, 1, 'final', 0)

BASE_DIR = Path(__file__).resolve().parent.parent

APP_PROFILE = os.environ.get('APP_PROFILE', 'local')

_PROFILE_DEFAULTS = {
    'local': {
        'DEBUG': 'True',
        'ALLOWED_HOSTS': 'localhost,127.0.0.1',
        'DB_HOST': 'localhost',
        'DB_USER': 'app',
        'DB_PASSWORD': '1234',
        'BACK_HOST': 'localhost',
        'BACK_PORT': '8080',
    },
    'dev': {
        'DEBUG': 'True',
        'ALLOWED_HOSTS': '*',
        'DB_HOST': 'boterview-mysql',
        'DB_NAME': 'boterview',
        'DB_USER': 'root',
        'DB_PASSWORD': 'root',
        'BACK_HOST': 'boterview-app',
        'BACK_PORT': '8080',
    },
    'prod': {
        'DEBUG': 'False',
        'ALLOWED_HOSTS': '',
        'DB_USER': 'root',
        'DB_PASSWORD': '',
        'BACK_HOST': 'boterview-app',
        'BACK_PORT': '8080',
    },
}

_profile = _PROFILE_DEFAULTS.get(APP_PROFILE, {})


def _env(key, fallback=''):
    return os.environ.get(key, _profile.get(key, fallback))


SECRET_KEY = _env('SECRET_KEY', 'django-insecure-gz^kh-ts6!a_m8c(il#79!0uf81a3rnr2v=eeh)!38s51-!o9_')

DEBUG = _env('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = [h for h in _env('ALLOWED_HOSTS').split(',') if h]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'ai_bot',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ai.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'ai.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': _env('DB_NAME', 'app'),
        'USER': _env('DB_USER', 'root'),
        'PASSWORD': _env('DB_PASSWORD'),
        'HOST': _env('DB_HOST', 'localhost'),
        'PORT': _env('DB_PORT', '3306'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

# Backend server connection
BACK_HOST = _env('BACK_HOST', 'localhost')
BACK_PORT = _env('BACK_PORT', '8080')
BACKEND_API_URL = f"http://{BACK_HOST}:{BACK_PORT}"

# OpenAI Settings
OPENAI_API_KEY = _env('OPENAI_API_KEY')

# AWS S3 Settings
AWS_ACCESS_KEY_ID = _env('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = _env('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = _env('AWS_STORAGE_BUCKET_NAME')
AWS_S3_REGION_NAME = _env('AWS_S3_REGION_NAME', 'ap-northeast-2')
