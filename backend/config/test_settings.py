"""Test settings.

Identical to :mod:`config.settings` except the database. Database-backed
tests run on SQLite -- faster and more portable for CI/local runs than
building a throwaway MSSQL database, while exercising the same models and
migrations.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                       "LOCATION": "gst-tally-tests"}}
