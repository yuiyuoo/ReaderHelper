FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -m spacy download en_core_web_sm \
 && python -m spacy download de_core_news_sm

COPY backend ./backend
COPY frontend ./frontend

# Run as a normal user; app.db, books/ and covers/ live in the /data volume.
RUN useradd --create-home --uid 1000 reader \
 && mkdir -p /data && chown reader:reader /data
USER reader
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/profiles', timeout=4)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
