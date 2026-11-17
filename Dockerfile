# Rub rub: a zero-dependency Python app, so there is nothing to pip install.
#
#   docker build -t rub-rub .
#   docker run --rm -p 8000:8000 -v rubrub-data:/data rub-rub
#
# Hosting platforms (Render, Railway, Fly.io...): build from this Dockerfile, mount a persistent
# disk/volume at /data, and set RUBRUB_TRUST_PROXY=1. See "Deploy" in README.md.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUBRUB_HOST=0.0.0.0 \
    RUBRUB_DATA_DIR=/data \
    RUBRUB_RUN_AS_UID=10001

WORKDIR /app
COPY rubrub.py db.py ./
COPY public ./public

# The app starts as root only so it can fix the ownership of a freshly mounted volume (hosts
# usually mount them root-owned), then it switches to the unprivileged user 10001.
RUN mkdir -p /data && chown 10001:10001 /data
VOLUME ["/data"]

# Platforms set PORT at run time; locally it is 8000.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz',timeout=4)"]

CMD ["python", "rubrub.py"]
