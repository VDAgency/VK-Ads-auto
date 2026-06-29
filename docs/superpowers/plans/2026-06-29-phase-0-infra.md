# Фаза 0 — Инфраструктура, репозиторий, скелет ядра — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять каркас проекта VK-Ads-auto: публичный git-репозиторий, структура папок, тулчейн качества (ruff/mypy/pytest), скелет headless-ядра на FastAPI с `/health` и `/api/v1`, швы мульти-тенанта, docker-compose-скелет и CI «только проверки» — без выкатки на сервер.

**Architecture:** Headless-ядро на FastAPI отдаёт версионированный внутренний API `/api/v1`; вся бизнес-логика в `core/`/`services/`; каналы (бот, веб) — тонкие клиенты. Исходящие интеграции скрыты за `PlatformAdapter`. Мульти-тенант заложен швами: `account_id` в базовой модели, per-account конфиг интеграций. Всё запускается в Docker Compose (FastAPI, PostgreSQL, Redis, n8n, бот).

**Tech Stack:** Python 3.11 (через uv), FastAPI, SQLAlchemy 2 (declarative, async), Pydantic v2 / pydantic-settings, ruff, mypy (strict), pytest + httpx, Docker Compose, GitHub Actions.

## Global Constraints

- Python **3.11** через **uv** (системный Python 3.13 — учитывать, фиксировать версию в `pyproject.toml`/`.python-version`).
- Репозиторий **ПУБЛИЧНЫЙ** → НИКАКИХ секретов в git: только `.env` (в `.gitignore`) и `.env.example` без значений.
- Гейт качества (обязан быть зелёным перед коммитом): `ruff check .` + `ruff format --check .` + `mypy .` + `pytest -q`.
- Git-процесс: НИКОГДА не коммитить в `main` напрямую — только feature-ветка + PR. Не пушить непрошедший тесты код.
- Headless-инвариант: бизнес-логика только в `core/`/`services/`; в роутерах/хендлерах каналов нет SQL и нет бизнес-логики.
- Исходящее — только через `PlatformAdapter`.
- Мульти-тенант: `account_id` на всех таблицах, конфиг интеграций per-account.
- `.gitattributes`: `* text=auto eol=lf` (машина на Windows).
- CI на этом этапе — **только проверки** (ruff/mypy/pytest). Шага деплоя/SSH/секретов деплоя НЕТ (заблокировано до доступа к серверу).

---

## Группа A — ВЫПОЛНИМО СЕЙЧАС (без сервера и секретов)

### Task A1: Инициализация репозитория, gitignore, gitattributes

**Files:**
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `.python-version` (`3.11`)
- Create: `README.md` (краткое описание + ссылки на доки)

**Interfaces:**
- Produces: чистый git-репозиторий на ветке (не `main`) с базовыми ignore/attributes.

- [ ] **Step 1:** `git init`, переключиться на ветку `chore/phase-0-scaffold` (не работать в `main`).
- [ ] **Step 2:** Написать `.gitignore`: `.env`, `.env.*` (кроме `.env.example`), `__pycache__/`, `*.py[cod]`, `.venv/`, `venv/`, `node_modules/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `dist/`, `build/`, `*.egg-info/`, `.idea/`, `.vscode/`, дампы БД, `*.log`, SSH-ключи (`*.pem`, `id_*`).
- [ ] **Step 3:** Написать `.gitattributes`: `* text=auto eol=lf`.
- [ ] **Step 4:** `.python-version` = `3.11`; короткий `README.md`.
- [ ] **Step 5:** Проверить, что `.env` НЕ трекается (`git status` пуст по `.env`). Commit: `chore: init repo, gitignore, gitattributes`.

### Task A2: Структура папок ядра

**Files:**
- Create: `core/__init__.py`, `bot/__init__.py`, `services/__init__.py`, `integrations/__init__.py`, `db/__init__.py`, `tests/__init__.py`
- Create: `web/.gitkeep`, `infra/.gitkeep`, `n8n/workflows/.gitkeep`, `config/__init__.py`

**Interfaces:**
- Produces: дерево пакетов по PROJECT.md: `core/ bot/ services/ integrations/ db/ web/ tests/ infra/ n8n/workflows/ config/`.

- [ ] **Step 1:** Создать пакеты Python (`__init__.py`) и плейсхолдеры (`.gitkeep`) для не-Python папок.
- [ ] **Step 2:** Commit: `chore: project package structure`.

### Task A3: pyproject.toml + тулчейн (ruff/mypy/pytest)

**Files:**
- Create: `pyproject.toml`
- Create: `tests/test_sanity.py`

**Interfaces:**
- Produces: рабочий гейт качества; команды `ruff check .`, `ruff format --check .`, `mypy .`, `pytest -q`.

- [ ] **Step 1:** `pyproject.toml`: `requires-python = ">=3.11,<3.12"`; зависимости рантайма (fastapi, uvicorn[standard], pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, redis); dev (ruff, mypy, pytest, httpx, anyio); конфиг `[tool.ruff]` (target py311, выбор правил), `[tool.mypy]` (strict, python_version 3.11), `[tool.pytest.ini_options]`.
- [ ] **Step 2:** Написать падающий sanity-тест `tests/test_sanity.py::test_truth` (`assert 1 + 1 == 2`).
- [ ] **Step 3:** `uv sync` (или `uv venv` + установка). Прогнать `pytest -q` → PASS.
- [ ] **Step 4:** Прогнать `ruff check .` + `ruff format --check .` + `mypy .` → зелёные.
- [ ] **Step 5:** Commit: `chore: pyproject with ruff/mypy/pytest toolchain`.

### Task A4: Конфигурация через env (pydantic-settings)

**Files:**
- Create: `config/settings.py`
- Create: `.env.example`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings) — читает env; `get_settings()` с кешем. Поля: `app_env`, `database_url`, `redis_url` (без секретов в дефолтах).

- [ ] **Step 1:** Падающий тест: `get_settings()` читает `APP_ENV` из окружения, дефолт `local`.
- [ ] **Step 2:** Реализовать `Settings` + `get_settings()`; источник — env/`.env`. Никаких хардкод-секретов.
- [ ] **Step 3:** `.env.example` — перечень переменных БЕЗ значений (DATABASE_URL, REDIS_URL, BOT_TOKEN, VK_CLIENT_ID, VK_CLIENT_SECRET, GOOGLE_*, SENLER_*, N8N_API_URL, N8N_API_KEY, N8N_ENCRYPTION_KEY) с комментариями-плейсхолдерами.
- [ ] **Step 4:** Гейт зелёный. Commit: `feat: env-based settings + .env.example`.

### Task A5: Каркас FastAPI + `/health`

**Files:**
- Create: `core/app.py` (фабрика `create_app()`)
- Create: `core/api/__init__.py`, `core/api/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `get_settings()` из A4.
- Produces: `create_app() -> FastAPI`; `GET /health` → `200 {"status": "ok"}`.

- [ ] **Step 1:** Падающий тест (httpx + ASGITransport): `GET /health` → 200, тело `{"status": "ok"}`.
- [ ] **Step 2:** Реализовать `create_app()` и роутер health.
- [ ] **Step 3:** `pytest -q` → PASS; гейт зелёный.
- [ ] **Step 4:** Commit: `feat: FastAPI app factory with /health`.

### Task A6: Скелет внутреннего API `/api/v1`

**Files:**
- Create: `core/api/v1/__init__.py`, `core/api/v1/router.py`
- Modify: `core/app.py` (подключить v1-роутер с префиксом `/api/v1`)
- Test: `tests/test_api_v1.py`

**Interfaces:**
- Consumes: `create_app()` из A5.
- Produces: версионированный роутер `/api/v1`; служебный `GET /api/v1/ping` → `{"pong": true}` как шов контракта ядра.

- [ ] **Step 1:** Падающий тест: `GET /api/v1/ping` → 200, `{"pong": true}`.
- [ ] **Step 2:** Реализовать v1-роутер, подключить в `create_app()` с префиксом.
- [ ] **Step 3:** Гейт зелёный. Commit: `feat: versioned /api/v1 router skeleton`.

### Task A7: Швы мульти-тенанта в моделях (account_id)

**Files:**
- Create: `db/base.py` (declarative `Base` + `TenantMixin` с `account_id`)
- Create: `db/models.py` (минимальные `Account`, `Operator` со швом `account_id`)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Base`; `TenantMixin` (колонка `account_id: int`, индексируемая, NOT NULL — кроме самой `Account`); модели `Account`, `Operator`. Конфиг интеграций — спроектировать как per-account (`IntegrationConfig` с `account_id`), без глобальных ключей.

- [ ] **Step 1:** Падающий тест: у модели `Operator` есть атрибут-колонка `account_id`; `TenantMixin` добавляет `account_id` любой наследующей модели.
- [ ] **Step 2:** Реализовать `Base`, `TenantMixin`, `Account`, `Operator` (SQLAlchemy 2 declarative, типизированные `Mapped[...]`). Без подключения к живой БД — только модели/метаданные.
- [ ] **Step 3:** `mypy` строгий проходит на моделях; `pytest -q` → PASS.
- [ ] **Step 4:** Commit: `feat: multi-tenant model seam (account_id mixin)`.

### Task A8: PlatformAdapter — интерфейс (шов исходящего слоя)

**Files:**
- Create: `integrations/adapter.py` (Protocol/ABC `PlatformAdapter`)
- Test: `tests/test_adapter_contract.py`

**Interfaces:**
- Produces: `PlatformAdapter` с абстрактными `create_cabinet`, `create_campaign`, `upload_creative`, `launch`, `get_stats` (сигнатуры-заглушки с типами). Реальных реализаций (VK/kotbot) НЕТ на этом этапе — только контракт.

- [ ] **Step 1:** Падающий тест: нельзя инстанцировать `PlatformAdapter` напрямую; наследник, реализующий методы, — можно.
- [ ] **Step 2:** Реализовать ABC с пятью методами (типизированные сигнатуры, докстроки).
- [ ] **Step 3:** Гейт зелёный. Commit: `feat: PlatformAdapter outgoing-layer contract`.

### Task A9: docker-compose.yml (скелет)

**Files:**
- Create: `docker-compose.yml`
- Create: `Dockerfile` (для FastAPI-ядра/бота)
- Create: `.dockerignore`

**Interfaces:**
- Produces: compose-скелет сервисов `api` (FastAPI), `postgres`, `redis`, `n8n`, `bot`. Все секреты/пароли — через `${VAR}` из `.env`, без значений в файле. Поднимать сервер НЕ требуется — только корректный синтаксис.

- [ ] **Step 1:** `Dockerfile` (python:3.11-slim, установка через uv, запуск uvicorn). `.dockerignore` (`.git`, `.venv`, кеши).
- [ ] **Step 2:** `docker-compose.yml`: 5 сервисов, переменные через `${...}`, volume для postgres и n8n, healthcheck `api` на `/health`. Без хардкод-паролей.
- [ ] **Step 3:** `docker compose config` (валидация синтаксиса) → без ошибок. (Если Docker недоступен в сессии — отметить и проверить синтаксис вручную/линтером.)
- [ ] **Step 4:** Проверить diff на отсутствие секретов. Commit: `chore: docker-compose skeleton (api/pg/redis/n8n/bot)`.

### Task A10: CI workflow — ТОЛЬКО проверки (без деплоя)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: тулчейн из A3.
- Produces: GitHub Actions job на `push`/`pull_request`: setup uv + Python 3.11 → `ruff check .` → `ruff format --check .` → `mypy .` → `pytest -q`. **Без job деплоя, без SSH, без секретов деплоя.**

- [ ] **Step 1:** Написать `ci.yml` (триггеры push/PR; один job `quality`; шаги — установка uv, sync, четыре проверки гейта).
- [ ] **Step 2:** Проверить, что в workflow нет ссылок на деплой/SSH/секреты. Commit: `ci: quality gate (ruff/mypy/pytest), no deploy`.

### Task A11: Финал — гейт, PR, чекбоксы ROADMAP

**Files:**
- Modify: `docs/ROADMAP.md` (отметить выполнимые сейчас пункты Фазы 0)

- [ ] **Step 1:** Прогнать полный гейт локально: `ruff check .` + `ruff format --check .` + `mypy .` + `pytest -q` — всё зелёное (приложить вывод).
- [ ] **Step 2:** Отметить `[x]` в ROADMAP по фактически выполненным пунктам Фазы 0; заблокированные оставить `[ ]` с пометкой.
- [ ] **Step 3:** Создать публичный GitHub-репозиторий (через `gh`/MCP github), запушить ветку, открыть PR в `main`. НЕ мержить без зелёного CI.
- [ ] **Step 4:** Дождаться зелёного CI на PR.

---

## Группа B — ЗАБЛОКИРОВАНО (ждёт доступ к VPS и реальные данные)

> Эти пункты Фазы 0 НЕ выполняются сейчас. Причина — нет доступа к серверу Анастасии и в `docs/DEPLOY.md` сейчас плейсхолдеры (`SERVER_IP`, `SSH_USER`, путь проекта, имена ключей `<VK-Ads-auto>` / `<VK-Ads-auto-deploy>`). Имена ключей и реквизиты НЕ выдумывать — запросить у Вячеслава.

- [ ] **B1. Поднять Beget VPS, установить Docker + Docker Compose.** Блокер: нет VPS/доступа.
- [ ] **B2. Сгенерировать 2 SSH-ключа** с именами из `docs/DEPLOY.md` (Ключ 1 — разработчик↔сервер; Ключ 2 — Actions↔сервер). Блокер: имена в DEPLOY.md — плейсхолдеры; нужно подтверждение реальных имён.
- [ ] **B3. Внести деплой-секреты в GitHub Actions Secrets** (`SSH_DEPLOY_KEY`, `SERVER_IP`, `SSH_USER`) и серверный `.env`. Блокер: нет ключей и реквизитов сервера.
- [ ] **B4. Добавить в CI job деплоя:** SSH на Beget → `git pull` (HTTPS) → `docker compose pull && up -d --build` → миграции → проверка `/health`. Блокер: зависит от B1–B3.
- [ ] **B5. Проверить `/health` на сервере после автодеплоя.** Блокер: зависит от B1–B4.
- [ ] **B6. Развернуть n8n на сервере** (установщик kossakovsky, минимальный набор), `N8N_ENCRYPTION_KEY` + volume, создать `N8N_API_KEY`. Блокер: зависит от B1; детали — `docs/N8N.md`.
- [ ] **B7. Решить вопрос БД для n8n** (общий Postgres vs отдельный инстанс) — `docs/N8N.md §5`. Блокер: решается при настройке сервера.

---

## Self-Review

**Spec coverage (Фаза 0 из ROADMAP):**
- Публичный репозиторий → A11. Структура папок → A2. Контракт внутреннего API `/api/v1` → A6. Швы мульти-тенанта → A7. `pyproject.toml` + ruff/mypy/pytest → A3. `docker-compose.yml` (api/pg/redis/n8n/bot) → A9. `.env.example` → A4. `/health` → A5. CI smoke/проверки → A10. `PlatformAdapter` шов → A8 (сверх минимума ROADMAP, но требуется инвариантом 1.3). `.gitignore`/`.gitattributes`/`.env.example` без секретов → A1/A4.
- Заблокированное: Beget VPS, SSH-деплой в Actions, деплой-секреты, реальные имена ключей, n8n на сервере → группа B (соответствует п. 4 задания пользователя).

**Placeholder scan:** код в шагах описан конкретными файлами/командами; намеренно не вписаны секреты и реальные имена ключей (это запрет, а не недосказанность).

**Type consistency:** `account_id` (A7) используется единообразно; методы `PlatformAdapter` (A8) совпадают с PROJECT.md §5.
