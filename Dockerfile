FROM python:3.12-slim

# ffmpeg нужен только для конвертации аудио/видео в файловом менеджере
ARG WITH_FFMPEG=false
RUN if [ "$WITH_FFMPEG" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*; \
    fi

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    STORAGE_DIR=/data/storage \
    PERSONAL_DIR=/data/personal

RUN useradd --create-home --uid 1000 homecloud && mkdir /data && chown homecloud:homecloud /data
WORKDIR /srv/homecloud

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

USER homecloud
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
