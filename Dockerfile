# Ables Home Healthcare on Google Cloud Run
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets $PORT (default 8080). Single worker + threads is enough
# for this site's traffic; Flask-SQLAlchemy sessions are thread-local.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 wsgi:app
