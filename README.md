# Campus Flow — расписание БВТ2302

Сегодня, расписание, работы с личными статусами, страницы предметов, уведомления и Telegram для учебной группы МТУСИ. Регистрация по приглашению, роли, личные вопросы, TOTP, публичный календарь и отдельная офлайн-копия сохраняются. Интерфейс на русском и английском; пять вариантов темы.

[Новые учебные разделы и обновление](docs/STUDY_HUB_RU.md) · [Уведомления и Telegram](docs/NOTIFICATIONS_RU.md) · [Production для 2 ГБ RAM](docs/DEPLOY_TIMEWEB40_RU.md).

Production использует готовые SHA-образы, три отдельные базы в одном PostgreSQL и ограниченные ресурсы. Поддерживаются проверяемые копии, внешнее ежедневное резервирование и проверка совместимости отката. Импорт расписания не добавлен.

**Начните с [START_HERE_RU.md](START_HERE_RU.md).** Там описаны запуск, создание первого администратора и заполнение настоящего расписания.

## Быстрый локальный запуск

Команды этого раздела собирают образы и предназначены для разработки/локального знакомства. Для небольшого рабочего сервера используйте `compose.production.yaml` и `scripts/deploy_production.sh` по инструкции выше: там образы скачиваются, а не собираются.

Установите и запустите Docker с Compose v2. На Windows выберите Linux containers. Для Windows подходит Docker Desktop; на Linux можно использовать Docker Engine с Compose plugin.

**Windows:** распакуйте ZIP целиком, откройте папку `campus-flow` и запустите `START_WINDOWS.cmd` двойным щелчком. Он создаст `.env` и запустит контейнеры. Не открывайте файлы прямо внутри ZIP.

**Linux:** откройте терминал в папке проекта:

```bash
bash scripts/start.sh
```

Адрес по умолчанию: **http://localhost:8080**. Для создания первого администратора скопируйте значение `SETUP_KEY` из созданного `.env` в форму первого запуска. Email и пароль выберите сами; готового пароля администратора нет.

После первичной настройки обычный запуск:

```bash
docker compose up --build -d --wait
```

## Что реализовано

- Три API: учётные записи, расписание с работами, уведомления. Раздельные базы и роли; production — один экземпляр PostgreSQL, development — три.
- Сегодня: текущая/следующая пара, время до неё, занятия и ближайшие сроки.
- Расписание: день, неделя, месяц, сохранённая подгруппа, компактный вид и сворачивание дней.
- Работы: сроки, материалы, подгруппы и приватные статусы выполнения.
- Предметы: преподаватели, требования, HTTPS-материалы, занятия и работы.
- Подробные уведомления изменений, Web Push, Telegram, тихие часы и напоминания о сроках.
- Приглашения, подтверждение участников, роли, личные вопросы и важные объявления.
- Предпросмотр правок шаблона, удаление отдельных занятий, TOTP и управление сеансами.
- Публичная подписка календаря, iCalendar и явно сохраняемая офлайн-копия публичного расписания.
- Docker Compose, Windows/Linux запуск, резервирование, восстановление и CI.

**Начальная база пуста.** Реальное расписание БВТ2302 не предоставлялось. Демонстрационные предметы добавляются только кнопкой «Попробовать пример» в управлении и всегда помечаются как примеры. Они не являются расписанием университета.

## Документация

| Файл | Содержание |
| --- | --- |
| [START_HERE_RU.md](START_HERE_RU.md) | Подробный первый запуск и работа старосты |
| [docs/STUDY_HUB_RU.md](docs/STUDY_HUB_RU.md) | Сегодня, работы, предметы, обновление и внешние копии |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Границы сервисов, данные, согласованность |
| [docs/TIMEWEB_CLOUD_RU.md](docs/TIMEWEB_CLOUD_RU.md) | Сервер Timeweb, бюджет, домен и безопасность |
| [docs/DEPLOY_TIMEWEB40_RU.md](docs/DEPLOY_TIMEWEB40_RU.md) | Готовые образы, перенос отдельных БД в один PostgreSQL, обновление, backup/restore и rollback |
| [docs/PRODUCTION_VALIDATION_RU.md](docs/PRODUCTION_VALIDATION_RU.md) | Проверки production-профиля и фактические измерения CI |
| [docs/AUTO_TRANSLATION_RU.md](docs/AUTO_TRANSLATION_RU.md) | Автоперевод и обновление базы |
| [docs/OPERATIONS_RU.md](docs/OPERATIONS_RU.md) | Домен, HTTPS, бэкапы, восстановление, диагностика |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | API, события, iCalendar и дальнейшие интеграции |
| [docs/VALIDATION_RU.md](docs/VALIDATION_RU.md) | Что проверено и ограничения проверки |
| [docs/GIT_RU.md](docs/GIT_RU.md) | Продолжение разработки и публикация в ваш GitHub |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Источники и лицензии SVG / библиотек |

## Разработка без Docker

Python 3.12 и Node.js 22. SQLite здесь служит только локальной разработке; Compose использует PostgreSQL.

```bash
python3 -m venv .venv
.venv/bin/pip install -r services/requirements.lock
.venv/bin/python scripts/dev.py
```

Во втором терминале:

```bash
cd apps/web
npm ci
npm run dev
```

Автоперевод требует контейнера модели из Compose; здесь доступны исходные названия и ручные переводы. Откройте http://localhost:5173. Ключ первого запуска только для разработки: `local-development-setup-key`. Не используйте `scripts/dev.py` для публичного размещения. На Windows замените `.venv/bin/python` на `.venv\Scripts\python.exe`, а `.venv/bin/pip` — на `.venv\Scripts\pip.exe`.

Проверки:

```bash
.venv/bin/python -m pytest -q -s
```

В `apps/web`: `npm test` и `npm run build`. CI дополнительно собирает и запускает весь Compose, выполняет тот же HTTP-сценарий с PostgreSQL и проверяет восстановление копии.

## English quick start

Extract the complete archive. On Windows run `START_WINDOWS.cmd`; on Linux run `bash scripts/start.sh`. Open http://localhost:8080, copy `SETUP_KEY` from the generated `.env`, and create your administrator account. Switch the interface to English, configure the semester in **Management → Semester**, add recurring classes, create invitations, approve students and appoint the representative/deputy. No real timetable or accounts are preloaded. PostgreSQL data lives in named Docker volumes. Never use `docker compose down --volumes` on a live installation unless you intend to erase its databases.

## Guest access and private questions (PR2)

Guests can browse a deliberately limited public timetable at /#guest. Approved members can send private questions to a selected head, deputy or administrator. Administrators can additionally hold a head/deputy group role. [Russian guide, privacy boundaries and migrations](docs/GUEST_QUESTIONS_ROLES_RU.md).
