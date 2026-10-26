# The production image for self hosting: the pinned Python packages (OpenCV 5 from requirements.lock), the built web
# app, and every model and sample clip, each fetched and checked by scripts/fetch_assets.py at build time.
#   docker compose up -d --build      (compose.yaml)
# Models already in models/ (from an earlier python scripts/fetch_assets.py) are reused and checked, not downloaded.

FROM node:22-bookworm-slim AS web
WORKDIR /src
COPY brand.json ./
COPY backend/watchful/data/plans.json backend/watchful/data/readers.json backend/watchful/data/
COPY docs/overview.svg docs/
COPY eval/results/bench-devbox_6cpu_no_text.json eval/results/build_measurements.json eval/results/
COPY web/package.json web/package-lock.json web/
RUN cd web && npm ci --no-audit --no-fund
COPY web/ web/
RUN cd web && npm run build

FROM python:3.12-slim-bookworm AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock && rm /tmp/requirements.lock

# only what fetch_assets.py reads, so a code change does not download the models again
FROM base AS assets
WORKDIR /app
COPY backend/watchful/__init__.py backend/watchful/config.py backend/watchful/
COPY backend/watchful/vision/__init__.py backend/watchful/vision/models.py backend/watchful/vision/
COPY scripts/fetch_assets.py scripts/
COPY assets/yolox/ assets/yolox/
COPY models/ models/
RUN python scripts/fetch_assets.py

FROM base
RUN useradd --system --uid 10001 --home-dir /app/data --shell /usr/sbin/nologin pehra
WORKDIR /app
COPY --from=assets /app/models models/
COPY --from=assets --chown=pehra /app/data/samples data/samples/
COPY brand.json alembic.ini LICENSE ./
COPY backend/watchful backend/watchful/
COPY backend/migrations backend/migrations/
COPY bridge bridge/
COPY scripts scripts/
COPY --from=web /src/web/dist web/dist/
RUN chown pehra /app/data
ENV PYTHONPATH=/app/backend PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 OPENCV_IO_MAX_IMAGE_PIXELS=40000000
USER pehra
EXPOSE 8000
VOLUME /app/data
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["uvicorn", "watchful.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "2097152"]
