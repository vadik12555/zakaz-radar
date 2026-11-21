FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# database and log: data/radar.db, data/radar.log (mount a persistent volume here or set DB_PATH)
RUN mkdir -p /app/data

CMD ["python", "main.py"]
