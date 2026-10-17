FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY therapy ./therapy
COPY app.py .
RUN useradd -r -u 10001 arna && mkdir -p /data && chown -R arna /app /data
USER arna
ENV SQLITE_PATH=/data/arnaai.db
EXPOSE 8080
# LLM calls are I/O bound: threads keep latency low on a small instance.
CMD exec gunicorn --bind :$PORT --workers 2 --threads 8 --timeout 90 "app:create_app()"
