# Chromium and its system libraries come with this image, matched to the
# playwright version pinned in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=8003

WORKDIR /service

COPY requirements.txt ./
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt \
    && playwright install chromium

COPY app ./app

RUN chown -R pwuser:pwuser /service
USER pwuser

EXPOSE 8003

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8003/health', timeout=2).status==200 else 1)"

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} ${UVICORN_ARGS}
