FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# All collected data (SQLite database + uploaded media) lives here. Mount a volume on it.
ENV DATA_DIR=/data \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000

CMD ["sh", "-c", "gunicorn -w 2 --threads 4 --timeout 180 -b 0.0.0.0:${PORT} app:app"]
