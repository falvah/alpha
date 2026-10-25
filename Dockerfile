FROM python:3.14.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/app/.data
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock && groupadd --gid 10001 cabinet && useradd --uid 10001 --gid cabinet --no-create-home cabinet
COPY --chown=cabinet:cabinet config/ ./config/
COPY --chown=cabinet:cabinet credit/ ./credit/
COPY --chown=cabinet:cabinet templates/ ./templates/
COPY --chown=cabinet:cabinet static/ ./static/
COPY --chown=cabinet:cabinet fixtures/ ./fixtures/
COPY --chown=cabinet:cabinet manage.py ./
COPY --chown=cabinet:cabinet scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN mkdir -p /app/.data /app/staticfiles && chown -R cabinet:cabinet /app/.data /app/staticfiles && chmod 700 /app/.data
USER cabinet
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=3)"
ENTRYPOINT ["sh", "scripts/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--no-control-socket", "--timeout", "45", "--max-requests", "1000", "--max-requests-jitter", "50", "--access-logfile", "-", "--error-logfile", "-"]
