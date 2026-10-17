FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY emotion ./emotion
COPY app.py .
RUN useradd -r -u 10001 arna && chown -R arna /app
USER arna
EXPOSE 8080
CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 30 app:app
