# Habits Mini App Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Отдельные профили и удобная Telegram Mini App у одного бота.
**Architecture:** Сохранить Service и SQLite владельца, добавить Hub участников,
личные привычки и aiohttp JSON API в том же event loop. Nginx обслуживает
статический UI и ограниченный proxy через выделенную Docker-сеть.
**Tech Stack:** Python 3.12, PTB 22.8, SQLite, aiohttp, HTML/CSS/JS, Nginx.
**Spec:** docs/superpowers/specs/2026-10-18-habits-mini-app-design.md

## Global Constraints

- UID 10001, read-only rootfs, 192 MiB, только /data и /tmp доступны для записи.
- Миграция сохраняет `/data/state.sqlite3`; приглашённые имеют отдельные базы.
- Telegram initData проверяется на сервере; срок 1 час; только членство.
- Максимум 10 профилей; приглашение одноразовое на 24 часа.
- Публикация только main, без автоматического production deploy.

## Review Focus

- Перезапуск после приглашения не теряет профиль или настройки.
- Коллизии числовых ID целей/наград не дают доступ к чужой базе.
- Двойной POST/кнопка не дублируют награду или списание монет.
- Отключённая/удалённая привычка не продолжает присылать напоминания.
- Web-прокси не допускает произвольные hostname, HTML ботов или cookies сайта.

## Task 1: Личные профили и привычки

Files: habitbot/members.py, service.py, store.py, tests/test_members.py.
- [ ] Написать и запустить тесты миграции/изоляции/одноразовых приглашений/личных дней.
- [ ] Реализовать Hub, привычки и настройку стандартных направлений.
- [ ] Прогнать все unittest бота и исправить регрессии.

## Task 2: Telegram и подписанный API

Files: habitbot/multi.py, miniapp.py, app.py, tests/test_miniapp.py, test_multi.py.
- [ ] Написать и запустить отрицательные проверки подписи, срока, membership, чужих ID.
- [ ] Реализовать JSON API, многопользовательскую маршрутизацию и планировщик.
- [ ] Проверить повторную покупку/выполнение, приглашение и сохранение после restart.

## Task 3: UI и Campus Flow

Files: bots/mandarin-habits/web/*, scripts/bot_runner.py, compose*.yaml,
infra/nginx.conf, infra/web.Dockerfile, services/auth/bots.py, workflow, docs.
- [ ] Проверить opt-in контракт тестами Runner (legacy/неверный origin/сеть).
- [ ] Реализовать UI, публичный путь, OPEN APP и изолированный web-proxy.
- [ ] Проверить реальные HTTP endpoints и браузер на mobile/desktop.
- [ ] Обновить инструкции migration/backup/deploy/BotFather.
- [ ] Прогнать suites, production frontend build, проверить diff и оформить PR.
