FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

LABEL org.opencontainers.image.source="https://github.com/SaitWors/campus-flow"
LABEL com.campus-flow.bot.storage="data-v1"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN install -d -o 10001 -g 10001 -m 0700 /data
COPY bot.py /app/bot.py

USER 10001:10001
CMD ["python", "/app/bot.py"]
