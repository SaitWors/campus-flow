# Campus Flow — расписание БВТ2302

Сайт учебной группы МТУСИ, 4 курс, направление 09.03.01. Расписание, календарь, очередь на сдачу работ, регистрация по приглашению, роли и администрирование. Интерфейс на русском и английском; светлая, тёмная и системная темы.

**Начните с [START_HERE_RU.md](START_HERE_RU.md).** Там описаны запуск, создание первого администратора и заполнение настоящего расписания.

## Быстрый запуск

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

- Три независимых API: учётные записи, расписание, очереди; у каждого своя PostgreSQL и свои данные.
- Недельное расписание и календарь месяца с переходами по датам, фильтрами типа занятия и подгруппы.
- Повторяемые пары на чётные, нечётные или все недели; чётность от настраиваемого опорного понедельника.
- Изменение одного занятия без изменения семестрового шаблона; перенос, отмена, ожидание подтверждения; очный, дистанционный и смешанный форматы.
- Защита от пересечения занятий одной подгруппы и перезаписи устаревших изменений.
- Приглашения с лимитом и сроком; подтверждение регистрации; студент, заместитель, староста, администратор.
- Очереди с лимитом, временем открытия, позицией, вызовом, сдачей, пропуском, выходом и повторной записью в конец.
- Транзакции и ограничения БД для одновременных записей; ключи повторных запросов; история действий.
- Настраиваемые темы, RU/EN, мобильная компоновка, SVG-иконки Lucide, экспорт iCalendar.
- Docker Compose, Windows/Linux скрипты, резервные копии, CI и история Git.

**Начальная база пуста.** Реальное расписание БВТ2302 не предоставлялось. Демонстрационные предметы добавляются только кнопкой «Попробовать пример» в управлении и всегда помечаются как примеры. Они не являются расписанием университета.

## Документация

| Файл | Содержание |
| --- | --- |
| [START_HERE_RU.md](START_HERE_RU.md) | Подробный первый запуск и работа старосты |
| [docs/QUEUE_RULES_RU.md](docs/QUEUE_RULES_RU.md) | Поведение очереди по сценариям |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Границы сервисов, данные, согласованность |
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

Откройте http://localhost:5173. Ключ первого запуска только для разработки: `local-development-setup-key`. Не используйте `scripts/dev.py` для публичного размещения. На Windows замените `.venv/bin/python` на `.venv\Scripts\python.exe`, а `.venv/bin/pip` — на `.venv\Scripts\pip.exe`.

Проверки:

```bash
.venv/bin/python -m pytest -q -s
```

В `apps/web`: `npm test` и `npm run build`. CI дополнительно собирает и запускает весь Compose, выполняет тот же HTTP-сценарий с PostgreSQL и проверяет восстановление копии.

## English quick start

Extract the complete archive. On Windows run `START_WINDOWS.cmd`; on Linux run `bash scripts/start.sh`. Open http://localhost:8080, copy `SETUP_KEY` from the generated `.env`, and create your administrator account. Switch the interface to English, configure the semester in **Management → Semester**, add recurring classes, create invitations, approve students and appoint the representative/deputy. No real timetable or accounts are preloaded. PostgreSQL data lives in named Docker volumes. Never use `docker compose down --volumes` on a live installation unless you intend to erase its databases.
