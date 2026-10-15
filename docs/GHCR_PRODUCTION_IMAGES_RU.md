# Исправление ошибки «Download prebuilt images failed»

Скрипт `scripts/deploy_production.sh` **не собирает** образы на VPS. Для продакшена ему нужны две готовые версии:

- `ghcr.io/saitwors/campus-flow-api:<FULL_MAIN_SHA>`
- `ghcr.io/saitwors/campus-flow-web:<FULL_MAIN_SHA>`

API-образ используется тремя сервисами (auth, schedule, notifications), Web — для nginx/React. Скрипт `production.py` проверяет SHA-метки образов и совместимость схемы БД **перед** миграциями. Ошибка скачивания останавливает обновление до переключения работающих контейнеров.

## Теперь автоматическая публикация после проверок

Workflow `.github/workflows/publish-production.yml` запускается только при **успешном завершении** workflow «Campus Flow verification» от события `push` в `main` в этом же репозитории. Pull request, другие ветки и упавшие тесты не могут публиковать образы. Образы собираются на GitHub-hosted runners, а не на VPS.

Он собирает `infra/api.Dockerfile` и `infra/web.Dockerfile`, добавляет к каждому полную SHA-метку (в тег и метаданные) и публикует в GHCR. Для записи используется штатный `GITHUB_TOKEN` с `packages:write`. Секреты не прописаны в Git.

После merge нового PR обязательно дождитесь двух зелёных задач **Publish verified production images / publish (api)** и **publish (web)**. Успешный CI сам по себе НЕ означает, что образы уже опубликованы.

## Доступ к GHCR

По умолчанию новый GHCR-пакет может быть приватным. Если VPS работает без Docker login, владельцу необходимо отдельно сделать оба пакета публичными через GitHub Profile → Packages → Package settings → Change visibility → Public.

Это не делает данные студентов публичными: публикуются только уже открытые исходники и зависимости, без `.env.production` и содержимого БД. Но перед изменением видимости всё равно проверьте отсутствие секретов в Dockerfile и исходниках.

Если публичность не подходит, доступ к приватным образам можно выдать отдельно. Для ручного входа Docker CLI нужен токен с минимальным `read:packages`; **не публикуйте и не отправляйте токен в чат**. Важно: авторизация сервера на чтение и право GitHub Actions на публикацию — два независимых механизма. Если job publication выдаёт `permission_denied: write_package`, в настройках пакета нужно предоставить репозиторию `SaitWors/campus-flow` доступ к Actions.

Официальное описание: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry

## Безопасная проверка без остановки сайта

```bash
cd /opt/campus-flow
TAG=$(git rev-parse origin/main)
echo "Checking image tags for $TAG"
docker manifest inspect "ghcr.io/saitwors/campus-flow-api:$TAG" >/dev/null && echo "API manifest: OK"
docker manifest inspect "ghcr.io/saitwors/campus-flow-web:$TAG" >/dev/null && echo "WEB manifest: OK"
```

Если оба manifest доступны, запускать штатный `bash scripts/deploy_production.sh --image-tag "$TAG"`. Сначала он скачивает и проверяет образы, и только затем делает резервную копию и обновляет базы данных.

**Нельзя запускать `docker compose down -v`, удалять PostgreSQL-тома, менять `INTERNAL_TOKEN` или сбрасывать конфигурацию.** Если предыдущая попытка остановилась на скачивании образов, предварительная подготовка `BOT_RUNNER_TOKEN` уже выполнена; повторно генерировать его не требуется.

## Если ошибка повторилась

В приватном журнале `/opt/campus-flow/.production/last-error.log` есть точная причина. Не отправляйте полный файл, поскольку там могут оказаться приватные данные. Посмотрите локально на сервере и сообщите только категорию ошибки:

- `manifest unknown` или `not found` — у данной версии нет опубликованного тега;
- `unauthorized`, `denied` — пакет приватный или нет прав на чтение;
- `dial tcp`, `timeout`, `TLS handshake`, `DNS` — проблемы подключения;
- `permission_denied: write_package` возникает при публикации в GitHub Actions, а не при загрузке с VPS.

Если причина в сетевой доступности GHCR из VPS, настройка CI ничего не изменит: требуется отдельная проверка DNS, TLS и сетевой маршрутизации.
