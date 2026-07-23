FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="Komga Toolkit WebUI" \
      org.opencontainers.image.version="2.7.0" \
      org.opencontainers.image.description="WebUI Docker alignée sur Komga Toolkit Desktop V2" \
      io.komga-toolkit.reference="desktop-v3.12.0rc3-20260723"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KOMGA_TOOLKIT_DATA_DIR=/data \
    KOMGA_TOOLKIT_REFERENCE=desktop-v3.12.0rc3-20260723 \
    PYTHONPATH=/app
WORKDIR /app
RUN groupadd --gid 10001 komga && useradd --uid 10001 --gid komga --create-home komga
COPY docker/requirements-web.txt /tmp/requirements-web.txt
COPY docker/wheels/ /tmp/wheels/
RUN python -m pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r /tmp/requirements-web.txt \
    && rm -rf /tmp/wheels /tmp/requirements-web.txt \
    && mkdir -p /data \
    && chown -R komga:komga /data
COPY komga_db_tool/ ./komga_db_tool/
USER 10001:10001
WORKDIR /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "-c", "import json,urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)); assert payload.get('reference') == 'desktop-v3.12.0rc3-20260723'"]
CMD ["uvicorn", "komga_db_tool.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
