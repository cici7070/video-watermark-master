FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_RUNTIME_ROOT=/tmp/watermark-master

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /usr/sbin/nologin app
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install .

COPY api.py ./
COPY web ./web
RUN mkdir -p /tmp/watermark-master \
    && chown -R app:app /app /tmp/watermark-master

USER app
EXPOSE 10000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "10000", "--workers", "1", "--forwarded-allow-ips", "*"]
