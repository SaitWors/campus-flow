# PR3: проверка production-профиля

Дата: 16 сентября 2026. [PR #3](https://github.com/SaitWors/campus-flow/pull/3) · [инструкция эксплуатации](DEPLOY_TIMEWEB40_RU.md).

**НЕ ПРОВЕРЕНО НА РЕАЛЬНОЙ TIMEWEB 2 GB VM.** Ни SSH-подключение к рабочему серверу, ни изменение его баз, DNS или конфигурации не выполнялись. Ниже приведены результаты одноразовой среды GitHub Actions с настоящим PostgreSQL и применёнными лимитами контейнеров.

## Зафиксированный полный прогон

[Actions #25, run 35141473202](https://github.com/SaitWors/campus-flow/actions/runs/35141473202) завершился успешно на коде PR `71587a60aea41eae86c31424463060a8699ead8f`. GitHub проверял merge-ref `048a643f6dcc62581a275f26d2a9ffc209ea9b98`; он же указан в локально собранных образах этого прогона. Это не опубликованный GHCR-релиз.

| Задача | Результат | Содержание |
| --- | --- | --- |
| tests | success | 53 серверных теста на момент прогона, 9 клиентских тестов, TypeScript и сборка |
| lightweight | success | Четыре API/PostgreSQL без модели, HTTP-сценарии PR1–PR3, ручной перевод |
| compose | success | Полный прежний Compose, настоящий Argos, браузеры, backup/restore |
| production | success | Один PostgreSQL, реальные PR2-данные, ограничения ресурсов, браузер PR3, восстановление и откат |

[Артефакт production: журнал и скриншоты](https://github.com/SaitWors/campus-flow/actions/runs/35141473202/artifacts/10466160807). Приватные конфиги и дампы в артефакт не включены. Для загрузки артефакта может потребоваться вход в GitHub.

После этого прогона добавлены атомарное сохранение IMAGE_TAG, блокировка параллельных операций и два теста этих защит: локально **55 passed**. Итоговый HEAD и результаты повторного CI для него приведены в описании PR; таблица выше относится именно к указанному воспроизводимому прогону, а не автоматически к будущим коммитам.

## Что доказала новая задача

1. Из checkout настоящего PR2 `3d3b650ac6ea2ac3b9953b829b43a658a4e97270` собраны и запущены исходные четыре PostgreSQL и приложения. Созданы тестовые пользователи, расписание, очередь, объявления, личный вопрос с ответом и роль администратора-старосты.
2. Все четыре базы скопированы в новый экземпляр. До миграций сравниваются число и отпечатки строк **каждой** таблицы. После миграций повторно сравниваются все прежние столбцы; проверены схемы auth=3, schedule=3, queue=2, notifications=2, сохранение старой cookie и дополнительной роли, повторный запуск миграций.
3. Отдельная пустая production-установка проходит HTTP-сценарии регистрации, прав/CSRF, расписания, конкурентной очереди, уведомлений, вопросов, предметов/времени, предпросмотра, TOTP/резервных кодов и календаря. Проверены реальные параметры PostgreSQL через SHOW и пулы внутри каждого API-контейнера.
4. Проверены четыре отдельных логина БД, отсутствие SUPERUSER/CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS и CONNECT к чужим базам. Снаружи опубликован только web на loopback.
5. Chromium проверяет адаптивность, выбор предмета и перевода, ручную правку времени, сброс устаревшего предпросмотра, уменьшение движения, TOTP-вход и настоящую офлайн-перезагрузку/удаление публичной копии. Ширины: 360/390/768/1360 px; интерфейс RU/EN.
6. Полная копия восстановлена и сравнена в отдельных временных базах. Затем в одноразовом CI удалены схемы рабочих тестовых баз, выполнено восстановление и проверены пользователи, расписание, история очередей, объявления, личная переписка, предметы/интервалы/сеансы. Также пройден штатный операторский restore с предварительным бэкапом.
7. Выполнен откат на образ **из настоящего предыдущего checkout** `8074ee12fdd4b2b4ab6c1fb0b87c709813ace617`, повторная проверка данных, затем обновление вперёд и новая проверка. Образ не просто перетегирован. Несовместимая схема отдельно проверяется модульным тестом до любых записей.

Все записывающие проверки требуют `GITHUB_ACTIONS=true`, `CAMPUS_DISPOSABLE=1`, новых проектов `campus-ci-*` и отсутствия прежних томов. Только эти одноразовые проекты удаляются по завершении теста. Рабочие скрипты тома не удаляют и не запускают тестовое наполнение.

## Фактический снимок ресурсов

`docker stats --no-stream`, 19:41:15 UTC, после HTTP-сценариев на свежем production-профиле:

| Контейнер | CPU | RAM | Лимит RAM | Процессы |
| --- | ---: | ---: | ---: | ---: |
| auth | 0,06% | 65,03 МиБ | 160 МиБ | 4 |
| notifications | 15,98% | 86,14 МиБ | 192 МиБ | 4 |
| postgres | 0,03% | 60,83 МиБ | 384 МиБ | 14 |
| queue | 0,05% | 72,23 МиБ | 160 МиБ | 4 |
| schedule | 0,06% | 68,88 МиБ | 224 МиБ | 3 |
| web | 0,00% | 6,00 МиБ | 64 МиБ | 5 |
| **Сумма RAM** | | **359,11 МиБ** | **1184 МиБ** | |

Это один снимок Docker, **не измеренный пик**, не полная память хоста и не результат длительной нагрузки. Сумма лимитов не означает, что контейнеры постоянно занимают столько RAM. Контроль после восстановления/отката также прошёл без OOM и самопроизвольных перезапусков.

Вывод `free -h` в тот же момент:

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       1.8Gi       8.3Gi        77Mi       5.9Gi        13Gi
Swap:          3.0Gi          0B       3.0Gi
```

Сводка фактического `docker compose ps` в 19:41:13 UTC:

| Service | Status | Публикация порта |
| --- | --- | --- |
| auth | Up 51 seconds (healthy) | Нет; 8000/tcp только внутри Docker |
| notifications | Up 34 seconds (healthy) | Нет; 8000/tcp только внутри Docker |
| postgres | Up 57 seconds (healthy) | Нет |
| queue | Up 39 seconds (healthy) | Нет; 8000/tcp только внутри Docker |
| schedule | Up 45 seconds (healthy) | Нет; 8000/tcp только внутри Docker |
| web | Up 28 seconds (healthy) | 127.0.0.1:8080 → 8080/tcp |

У всех шести контейнеров `RestartCount=0`, `OOMKilled=false`; лимиты RAM/CPU/PIDs и ротация `10m × 3` подтверждены через Docker inspect. После завершения теста старые остановленные стенды в диагностике могут иметь сохранённый `unhealthy`: это вывод очистки, не состояние принимаемой работающей установки.

Короткая проверка чтения после HTTP-сценариев: среднее 1,96 мс, максимум 3,13 мс на localhost этого runner. Эти числа не характеризуют интернет-задержку, нагрузку группы, p95/p99 или скорость Timeweb.

## Исправление исходного сбоя браузера

Исходный CI падал на офлайн-перезагрузке. Основной HTML мог попадать в HTTP-кеш браузера, обходя ожидаемую отдельную офлайн-страницу. HTML теперь получает `Cache-Control: no-store`, а сетевые navigation-fetch Service Worker используют `cache: no-store`.

Отдельно воспроизведено поведение Chromium: одной эмуляции `context.setOffline(true)` недостаточно, чтобы гарантированно запретить все navigation-запросы Service Worker. На время офлайн-фазы тест также обрывает сетевые запросы маршрутизацией Playwright. Проверки открытия публичной страницы, данных, приватности и удаления сохранены; тест не отключён и не помечен необязательным. Полный Compose и production-задача проходят один и тот же сценарий.

## Файлы и коммиты

Основные добавления: `compose.production.yaml`, `.env.production.example`, `infra/init-production-databases.sql`, `infra/postgres-production.conf`, `.github/workflows/publish-images.yml`, `services/auth/passwords.py`, `scripts/production.py`, `scripts/prod_smoke.py`, `scripts/verify_production.py`, семь shell-команд обслуживания и тесты ограничений/операций. Изменены Dockerfile API/web, CI, настройки пулов/логов, Nginx, загрузчик Argos и документация.

[Полный список изменений](https://github.com/SaitWors/campus-flow/pull/3/files) · [Все коммиты PR](https://github.com/SaitWors/campus-flow/pull/3/commits). Основные этапы оптимизации:

| SHA | Изменение |
| --- | --- |
| `c52dfd7` | Исходные функции PR3 и полный прежний CI доведены до зелёного состояния |
| `8153fff` | Один PostgreSQL, лимиты ресурсов, малые пулы и ограничение параллелизма Argon2 |
| `0b1631d` | Публикация SHA-образов, `/version`, безопасные журналы и проверки чтения |
| `8074ee1` | Официальный адрес модели Argos с проверяемым резервным источником |
| `71587a6` | Перенос PR2, резервирование, восстановление, откат и отдельный production CI |

Последующие коммиты завершают защиту операций и эту документацию. Полные SHA итогового кода и повторного CI фиксируются в PR после публикации, чтобы не приписывать результаты прогонов ещё не проверенным изменениям.

### Полный перечень файлов PR3

```text
.dockerignore
.env.production.example
.github/workflows/ci.yml
.github/workflows/publish-images.yml
.gitignore
README.md
START_HERE_RU.md
apps/web/public/offline.css
apps/web/public/offline.html
apps/web/public/offline.js
apps/web/public/sw.js
apps/web/src/Admin.tsx
apps/web/src/App.tsx
apps/web/src/Auth.tsx
apps/web/src/CalendarTools.tsx
apps/web/src/Catalog.tsx
apps/web/src/LessonForm.tsx
apps/web/src/Security.tsx
apps/web/src/i18n.ts
apps/web/src/main.tsx
apps/web/src/motion.ts
apps/web/src/pr3.css
apps/web/src/pr3Copy.ts
apps/web/src/types.ts
apps/web/src/ui.tsx
compose.production.yaml
docs/ARCHITECTURE.md
docs/DEPLOY_FRIEND_RU.md
docs/DEPLOY_TIMEWEB40_RU.md
docs/OPERATIONS_RU.md
docs/PR3_RU.md
docs/PRODUCTION_VALIDATION_RU.md
docs/TIMEWEB_CLOUD_RU.md
infra/api.Dockerfile
infra/init-production-databases.sql
infra/nginx.conf
infra/postgres-production.conf
infra/web.Dockerfile
scripts/backup_production.sh
scripts/check_pr3_ui.cjs
scripts/check_timeweb40_budget.sh
scripts/deploy_production.sh
scripts/diagnose_production.sh
scripts/install_ru_en_model.py
scripts/migrate_four_dbs_to_single_postgres.sh
scripts/prod_smoke.py
scripts/production.py
scripts/restore_production.sh
scripts/rollback_production.sh
scripts/verify_pr3.py
scripts/verify_production.py
scripts/verify_restore.py
services/auth/main.py
services/auth/models.py
services/auth/passwords.py
services/auth/security.py
services/common/core.py
services/requirements.txt
services/schedule/catalog.py
services/schedule/main.py
services/schedule/models.py
tests/test_http.py
tests/test_model_download.py
tests/test_pr3.py
tests/test_pr3_migrations.py
tests/test_production_operations.py
tests/test_resource_limits.py
tests/test_version_observability.py
```

## Известные ограничения

- Нет измерений на реальной VM 2 ГБ, длительного теста нагрузки или проверки её swap/firewall/TLS/DNS. Запас памяти нужно подтвердить при установке.
- Публикация GHCR настроена, но выполняется только после слияния и успешного CI на `main`. В PR проверяются образы, собранные на runner; доступность опубликованных пакетов и pull на рабочем сервере ещё не подтверждены.
- Для API/web используются полные SHA-теги с запретом перезаписи workflow. Базовые Python/Node/Nginx/PostgreSQL-образы пока не зафиксированы digest; dedicated pip-audit/npm-audit/Trivy/Gitleaks jobs в этот этап не включены. Существующий digest/контрольная сумма переводчика сохранены. Это не заключение об отсутствии уязвимостей во всех зависимостях.
- Копии не шифруются самим скриптом и не выгружаются автоматически за пределы VM. Это задача эксплуатации; инструкция описывает перенос и проверку. Автоматического удаления старых копий/образов нет.
- Откат к PR2 требует схемы 2 и восстановления соответствующих данных. Проверенный image rollback относится к совместимому предыдущему PR3.
- Офлайн-копия публичная и обновляется пользователем вручную. Включение тяжёлого локального переводчика в профиль 2 ГБ не предусмотрено.
