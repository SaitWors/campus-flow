# Private Bot Manager — защищённый запуск Telegram-ботов

Функция доступна только первоначальному владельцу — аккаунту, зарегистрированному при первичной настройке Campus Flow через setup. Другие администраторы и старосты не получают доступа.

## Что умеет версия 1

- Существующая TOTP обязательна для владельца; отдельный новый шестизначный код требуется при входе в Bot Manager. Срок доступа 10 минут, сервер проверяет его на каждом API-запросе, токен сессии и CSRF обновляются при повышении прав.
- Регистрация, редактирование, запуск, остановка, удаление ботов, просмотр статуса и последних логов.
- Поддерживаются только проверенные опубликованные OCI-образы из GHCR личного аккаунта saitwors, по неизменяемому digest SHA-256. Чужие registry, теги вроде latest, загрузка ZIP и команды shell не допускаются.
- Не более 3 ботов. На каждый 192 MiB RAM, 0,35 CPU, 64 PID, non-root UID 10001, read-only rootfs, tmpfs для /tmp, отсутствие privileged/capabilities, без монтирования хостовых каталогов и без доступа к приватным сетям Campus Flow.
- Токен BotFather записывается в зашифрованное поле БД (ключ зависит от существующего INTERNAL_TOKEN). Токен не возвращается через интерфейс.
- Docker socket не передаётся вебу или auth API. Отдельный системный Bot Runner, устанавливаемый вручную на VPS, ограниченно управляет своими контейнерами по меткам.

## Ограничения и риски

Docker не является абсолютной границей безопасности для заведомо вредоносного кода. Запускайте только собственные проверенные образы; доступ root/Docker на хосте позволяет читать переменные окружения в контейнерах. Runner запускается с полномочиями Docker, поэтому его системный конфиг и 256-битный ключ хранятся только на сервере (0600).

В версии 1 нет WebAuthn/Passkeys, OAuth, ZIP-загрузки, автоматического деплоя из GitHub push, поддержки приватного GHCR и постоянных томов для ботов. Бот должен читать токен из переменной BOT_TOKEN, работать без записи в корневую файловую систему и без прав root. Для webhook-проектов понадобится отдельная настройка; рекомендуемый способ — Telegram long polling.

Если Runner не работает, ранее запущенные контейнеры могут продолжать работать. Для аварийной остановки всех управляемых ботов вручную:
    docker ps -q --filter label=campus-flow.bot-managed=true | xargs -r docker stop

Удаление бота удаляет его контейнер при следующем успешном опросе runner (20 секунд). Токены нельзя восстановить из зашифрованной БД при утрате прежнего INTERNAL_TOKEN.

## Как готовить собственный бот

На компьютере пишете Python-бота с aiogram или python-telegram-bot. Библиотеки перечисляете в requirements.txt, токен читаете из os.environ["BOT_TOKEN"]. Пример Dockerfile:

    FROM python:3.12-slim
    WORKDIR /app
    COPY requirements.txt /app/
    RUN pip install --no-cache-dir -r requirements.txt
    COPY bot.py /app/
    ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
    CMD ["python", "-u", "/app/bot.py"]

На отдельном GitHub-репозитории создаёте GitHub Actions сборку Docker и публикацию в ghcr.io/saitwors/<имя>. Используйте GitHub Actions packages: write и docker/build-push-action. После публикации используйте в панели digest из build-push-action: ghcr.io/saitwors/<имя>@sha256:<64 hex>.

Официальный пример: https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images

## После merge PR — активация в production

На VPS уже работает /opt/campus-flow/compose.production.yaml. Никогда не используйте docker compose down -v: тома содержат данные одногруппников.

1. Дождитесь успешной публикации новых Campus Flow API и web SHA-образов после merge в main.
2. Обновите чистый рабочий checkout в /opt/campus-flow до origin/main.
3. Выполните: bash scripts/install_bot_runner.sh prepare. Команда генерирует новый BOT_RUNNER_TOKEN, записывает его в .env.production и /etc/campus-flow-bot-runner.env (права 0600), но пока не запускает systemd runner.
4. Выполните: bash scripts/deploy_production.sh --image-tag <полный SHA main>. Штатный deploy делает резервирование данных, сверяет образы и применяет миграцию auth schema 4.
5. После успешного обновления: bash scripts/install_bot_runner.sh start.
6. Откройте Campus Flow -> Настройки -> Безопасность, включите TOTP, сохраните резервные коды. Управление -> Боты -> свежий шестизначный код -> добавьте образ и токен BotFather.

Диагностика:
    systemctl status campus-flow-bot-runner --no-pager
    journalctl -u campus-flow-bot-runner --since '10 minutes ago' --no-pager
    docker stats --no-stream
    vmstat 1 10
    df -h /

Проверка production: bash scripts/check_timeweb40_budget.sh

ВНИМАНИЕ: после миграции auth 3 -> auth 4 простой откат на старый образ не поддерживается; для отката необходимо восстановить совместимую согласованную резервную копию. Старые значения INTERNAL_TOKEN и SETUP_KEY не должны изменяться.

Техническая структура: services/auth/bots.py и models.py (права, токены, schema 4), BotManager.tsx (панель), scripts/bot_runner.py (ограниченный исполнитель), infra/campus-flow-bot-runner.service (локальный systemd). Сам Campus Flow остаётся в существующих пяти production-контейнерах.
