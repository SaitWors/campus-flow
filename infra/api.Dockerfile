FROM python:3.12-slim
ARG VCS_REF=development
ARG BUILD_DATE=unknown
ENV APP_COMMIT=$VCS_REF APP_BUILD_DATE=$BUILD_DATE
LABEL org.opencontainers.image.source="https://github.com/SaitWors/campus-flow" \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.created=$BUILD_DATE \
      io.campus-flow.schemas='{"auth":3,"schedule":3,"queue":2,"notifications":2}'
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY services/requirements.lock services/requirements.lock
RUN pip install --no-cache-dir -r services/requirements.lock && useradd --create-home --uid 10001 campus
COPY services services
COPY scripts scripts
USER campus
EXPOSE 8000
