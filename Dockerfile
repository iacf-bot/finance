# Optional container image (the app runs fine without Docker: ./start.sh).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FINANCE_IN_CONTAINER=1 FINANCE_HOSTS=0.0.0.0 FINANCE_PORT=8088 TZ=America/Los_Angeles
RUN apt-get update && apt-get install -y --no-install-recommends rclone tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY scripts ./scripts
VOLUME ["/app/data"]
EXPOSE 8088
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8088/healthz')" || exit 1
CMD ["python", "-m", "app.serve"]
