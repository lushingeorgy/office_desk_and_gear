FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt \
    && useradd --create-home --uid 10001 odg
COPY server ./server
COPY migrations ./migrations
COPY alembic.ini run_app.py ./
RUN mkdir -p /app/server/data && chown -R odg:odg /app
USER odg
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*' --workers 2"]
