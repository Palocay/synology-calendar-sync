FROM python:3.12-slim

ARG VERSION=dev

LABEL org.opencontainers.image.title="synology-calendar-sync" \
      org.opencontainers.image.description="Birthdays from Synology Contacts and ICS subscriptions for Synology Calendar" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TZ=Europe/Berlin \
    APP_VERSION=${VERSION}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 1000 --no-create-home app

COPY calsync/ calsync/

# Run as unprivileged user.
USER app

# Lightweight check: no library imports, just compares the deadline
# written after each successful run with the current time.
HEALTHCHECK --interval=1m --timeout=10s --start-period=1m --retries=3 \
  CMD ["python", "-c", "import sys,time; sys.exit(0 if time.time() <= float(open('/tmp/synology-calendar-sync.health').read()) else 1)"]

CMD ["python", "-m", "calsync", "--loop"]
