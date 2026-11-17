"""Vercel entry point.

vercel.json rewrites every /api/*, /uploads/* and /healthz request to this one function. It hands
the request to the same WSGI application that the built-in server uses (rubrub.wsgi_app), which keeps
accounts, reviews and photos in the Postgres database that Vercel injects as DATABASE_URL.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the project root: rubrub.py, db.py

from rubrub import wsgi_app as app  # noqa: E402  (Vercel looks for a WSGI callable named `app`)
