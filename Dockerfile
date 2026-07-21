FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite lives here; mount a volume at /data to persist across restarts.
ENV SRI_DB_PATH=/data/data.db
RUN mkdir -p /data

EXPOSE 8000

# Honour the platform's $PORT if provided (Render, Railway, Fly, etc.).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
