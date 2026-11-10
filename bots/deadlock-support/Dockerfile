FROM python:3.12-slim

LABEL com.campus-flow.bot.storage="data-v1"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
RUN mkdir -p /data && chown 10001:10001 /data
COPY bot.py messages.py store.py telegram_api.py ./

USER 10001:10001
CMD ["python", "bot.py"]
