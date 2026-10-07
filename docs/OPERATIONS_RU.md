# Обслуживание

Для рабочего сервера используйте [production-инструкцию](DEPLOY_TIMEWEB40_RU.md). `compose.production.yaml` — один PostgreSQL с тремя БД и готовыми SHA-образами. `compose.yaml` — разработка/локальный запуск, три экземпляра PostgreSQL со сборкой образов.

## Разработка и локальные копии

```bash
docker compose ps
docker compose logs -f schedule notifications
bash scripts/backup.sh
bash scripts/restore.sh backups/PLACEHOLDER
```

Для Windows используйте `backup.ps1` / `restore.ps1`. Восстановление просит `RESTORE` и заменяет данные. Копия содержит `auth.dump`, `schedule.dump`, `notifications.dump`, `materials.tar`, `config.env`, `effective-config.json`, `manifest.json`, `COMPLETE`. SHA-256 архива и каждого файла проверяются до восстановления; версии до schedule 5 могут восстанавливаться без материалов. Во время создания копии web и писатели останавливаются, затем возобновляются. Копия без COMPLETE неполная. Данные PostgreSQL и материалы хранятся в именованных томах: копирование исходников не копирует БД и файлы. [Материалы: квота, том, прокси и восстановление](MATERIALS_RU.md).

## Production

```bash
bash scripts/backup_production.sh
python3 scripts/production.py test-restore --backup backups/production-PLACEHOLDER
bash scripts/restore_production.sh --backup backups/production-PLACEHOLDER
bash scripts/diagnose_production.sh
bash scripts/check_timeweb40_budget.sh
```

Проверка восстановления создаёт отдельные временные базы и каталог материалов на диске, сравнивает отпечатки строк и SHA-256 файлов. Штатный restore сначала валидирует файлы и INTERNAL_TOKEN, затем сохраняет полную копию текущих БД и материалов. После импорта БД публикуется полный staged-набор файлов. Ошибка восстановления оставляет приложение остановленным. Откат образов разрешён только для совместимых схем.

[Ежедневная внешняя SSH-копия](STUDY_HUB_RU.md#автоматическая-внешняя-копия) задаётся отдельным systemd-таймером. Хранилище, ключ, известный SSH-хост и политика хранения настраиваются оператором. Автоматического удаления старых копий нет; следите за свободным диском и проверяйте восстановление.

После обновления запуск с `--remove-orphans` удаляет устаревшие контейнеры. Старые тома и история в старых копиях сохраняются. Не выполняйте `docker compose down --volumes` на рабочей установке.

HTTPS завершает внешний Caddy/Nginx; production web слушает loopback. Сохраните существующие секреты, задайте COOKIE_SECURE=true и правильный APP_ORIGIN. Файлы .env, копии и подробные ошибки приватны; не публикуйте их в логах или репозитории.
