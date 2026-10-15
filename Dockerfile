FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8020 \
    DATA_DIR=/app/data

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    imagemagick \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements-production.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY backend/app ./app
COPY backend/entrypoint.py ./entrypoint.py
RUN mkdir -p /app/data \
    && python -m compileall -q /app/app \
    && chown -R 10001:10001 /app

EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8020') + '/health', timeout=3)"

CMD ["python", "/app/entrypoint.py"]
