"""Django settings for the Yale Shuttle app.

Everything that differs between environments comes from environment variables
(12-factor); README.md lists them. For local development, DJANGO_DEBUG=1 uses
SQLite and a throwaway secret key unless DATABASE_URL and SECRET_KEY are set.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG") == "1"

SECRET_KEY = os.environ.get("SECRET_KEY") or (
    "django-insecure-local-development-only" if DEBUG else None
)
if not SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY, or DJANGO_DEBUG=1 for local development.")

ALLOWED_HOSTS = [host for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):  # set by Render
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "accounts",
    "abtest",
    "core",
    "transit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# The database is attached by URL. Neon's URLs end in ?sslmode=require.
if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.environ["DATABASE_URL"],
            conn_max_age=600,
            conn_health_checks=True,
            disable_server_side_cursors=True,  # also safe with Neon's pooled URLs
        )
    }
elif DEBUG:
    DATABASES = {"default": dj_database_url.parse(f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
else:
    raise ImproperlyConfigured("Set DATABASE_URL.")

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Not the Manifest variant: that one needs collectstatic before every test run.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Render terminates HTTPS and forwards plain HTTP with this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG

EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")

# Logs go to stdout, where Render collects them.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"stdout": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout"}},
    "root": {"handlers": ["stdout"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {"django": {"handlers": ["stdout"], "level": "INFO", "propagate": False}},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The live Downtowner feeds the transit app reads (issue #36).
DOWNTOWNER_URL = os.environ.get("DOWNTOWNER_URL", "https://yale.downtownerapp.com")
