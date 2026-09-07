import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "listings",
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
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.getenv("POSTGRES_DB", "home_hunter_dev"),
    "USER": os.getenv("POSTGRES_USER", "home_hunter"),
    "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
    "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
    "PORT": os.getenv("POSTGRES_PORT", "5432"),
}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation." + name}
    for name in ["UserAttributeSimilarityValidator", "MinimumLengthValidator",
                 "CommonPasswordValidator", "NumericPasswordValidator"]
]
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AVITO_PROXY_URL = os.getenv("AVITO_PROXY_URL", "")
AVITO_PERSISTENT_PROFILE = os.getenv("AVITO_PERSISTENT_PROFILE", "true").lower() == "true"
AVITO_FAST_SCAN_PAGES = int(os.getenv("AVITO_FAST_SCAN_PAGES", "1"))
AVITO_MAX_PAGES = int(os.getenv("AVITO_MAX_PAGES", "50"))
AVITO_NO_NEW_PAGES_LIMIT = int(os.getenv("AVITO_NO_NEW_PAGES_LIMIT", "2"))
# Navigation spacing also applies between searches and across collector runs.
AVITO_PAGE_DELAY_SECONDS = float(os.getenv("AVITO_PAGE_DELAY_SECONDS", "120"))
AVITO_BLOCK_COOLDOWN_SECONDS = float(os.getenv("AVITO_BLOCK_COOLDOWN_SECONDS", "3600"))
AVITO_STATE_DIR = Path(os.getenv("AVITO_STATE_DIR", str(BASE_DIR / ".avito-state")))
AVITO_FULL_SCAN_ENABLED = os.getenv("AVITO_FULL_SCAN_ENABLED", "false").lower() == "true"
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

CIAN_STATE_DIR = Path(os.getenv("CIAN_STATE_DIR", str(BASE_DIR / ".cian-state")))
CIAN_PROXY_URL = os.getenv("CIAN_PROXY_URL", "")
CIAN_FAST_SCAN_PAGES = int(os.getenv("CIAN_FAST_SCAN_PAGES", "1"))
CIAN_MAX_PAGES = int(os.getenv("CIAN_MAX_PAGES", "50"))
# Keep the legacy setting as the lower bound when it is explicitly configured.
CIAN_PAGE_DELAY_MIN_SECONDS = float(os.getenv(
    "CIAN_PAGE_DELAY_MIN_SECONDS", os.getenv("CIAN_PAGE_DELAY_SECONDS", "90"),
))
CIAN_PAGE_DELAY_MAX_SECONDS = float(os.getenv("CIAN_PAGE_DELAY_MAX_SECONDS", "210"))
CIAN_FULL_BATCH_PAGES = int(os.getenv("CIAN_FULL_BATCH_PAGES", "2"))
CIAN_BLOCK_COOLDOWN_SECONDS = float(os.getenv("CIAN_BLOCK_COOLDOWN_SECONDS", "3600"))
CIAN_FULL_SCAN_ENABLED = os.getenv("CIAN_FULL_SCAN_ENABLED", "false").lower() == "true"

DOMCLICK_STATE_DIR = Path(os.getenv("DOMCLICK_STATE_DIR", str(BASE_DIR / ".domclick-state")))
DOMCLICK_FAST_SCAN_PAGES = int(os.getenv("DOMCLICK_FAST_SCAN_PAGES", "1"))
DOMCLICK_MAX_PAGES = int(os.getenv("DOMCLICK_MAX_PAGES", "50"))
DOMCLICK_NO_NEW_PAGES_LIMIT = int(os.getenv("DOMCLICK_NO_NEW_PAGES_LIMIT", "2"))
DOMCLICK_PAGE_DELAY_SECONDS = float(os.getenv("DOMCLICK_PAGE_DELAY_SECONDS", "60"))
DOMCLICK_BLOCK_COOLDOWN_SECONDS = float(os.getenv("DOMCLICK_BLOCK_COOLDOWN_SECONDS", "3600"))
DOMCLICK_FULL_SCAN_ENABLED = os.getenv("DOMCLICK_FULL_SCAN_ENABLED", "false").lower() == "true"
