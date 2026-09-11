FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8002

WORKDIR /service

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN adduser --system --group --no-create-home service \
    && chown -R service:service /service
USER service

EXPOSE 8002

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/health', timeout=2).status==200 else 1)"

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} ${UVICORN_ARGS}
