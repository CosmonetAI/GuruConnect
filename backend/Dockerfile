FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 guruconnect && useradd --uid 10001 --gid 10001 --no-create-home guruconnect
WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY backend/recorder ./recorder
COPY backend/app ./app
COPY backend/supabase-ca.crt ./supabase-ca.crt
RUN test -s /app/backend/supabase-ca.crt
COPY frontend/scripts/migrate.py /app/frontend/scripts/migrate.py
COPY supabase/migrations /app/supabase/migrations
USER 10001:10001
EXPOSE 10000
CMD ["python", "-m", "recorder.launch"]
