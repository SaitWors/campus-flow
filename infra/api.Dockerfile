FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY services/requirements.lock services/requirements.lock
RUN pip install --no-cache-dir -r services/requirements.lock && useradd --create-home --uid 10001 campus
COPY services services
COPY scripts scripts
USER campus
EXPOSE 8000
