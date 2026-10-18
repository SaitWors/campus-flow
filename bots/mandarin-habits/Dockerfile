FROM python:3.12-slim
LABEL com.campus-flow.bot.storage="data-v1" com.campus-flow.bot.miniapp="habits-v1"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 habitbot \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin habitbot \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY --chown=10001:10001 habitbot ./habitbot
COPY --chown=10001:10001 bot.py owner.json ./
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-m", "habitbot.healthcheck"]
CMD ["python", "-u", "bot.py"]
