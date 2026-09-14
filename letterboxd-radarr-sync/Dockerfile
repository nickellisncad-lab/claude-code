FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATE_PATH=/data/state.db

WORKDIR /app

# Dependencies first so code edits do not bust the wheel cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Run unprivileged; /data is the only path the app needs to write.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /data /app
USER app

VOLUME ["/data"]

ENV PYTHONPATH=/app/src

ENTRYPOINT ["python", "-m", "lbxd_sync"]
