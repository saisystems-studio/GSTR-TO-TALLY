"""Test settings.

Identical to :mod:`config.settings` except the database. The MSSQL backend
cannot build a test database for this project: the third-party
``token_blacklist`` migration alters a column that a unique constraint depends
on, which SQL Server rejects (error 4922). Database-backed tests therefore run
on SQLite, which exercises the same models and migrations.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                       "LOCATION": "gst-tally-tests"}}
