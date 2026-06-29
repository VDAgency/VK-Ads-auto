# VK-Ads-auto

Система автоматизации запуска рекламы в VK Ads через Telegram-бот.

Оператор ставит задачу в боте → система принимает бриф клиента, раскладывает его в
настройки кампании, по подтверждению создаёт рекламный кабинет и запускает рекламу
на одну из 4 целей, затем собирает статистику.

## Архитектура (кратко)

- **Headless-ядро** (FastAPI): вся бизнес-логика в `core/`/`services/`, версионированный
  внутренний API `/api/v1`. Каналы (Telegram-бот, веб) — тонкие клиенты.
- **Исходящие интеграции** — через `PlatformAdapter` (VK Ads API / kotbot).
- **Мульти-тенант-швы**: `account_id` на всех таблицах, конфиг интеграций per-account.
- Развёртывание — Docker Compose на Beget VPS (РФ, под 152-ФЗ).

## Документация

- [`CLAUDE.md`](CLAUDE.md) — правила процесса разработки.
- [`PROJECT.md`](PROJECT.md) — архитектура и модель данных.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — план и порядок фаз.
- [`docs/BRIEF_SPEC.md`](docs/BRIEF_SPEC.md) — метод сборки брифа.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — обоснования решений.
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — правила деплоя.
- [`docs/N8N.md`](docs/N8N.md) — правила работы с n8n.

## Разработка

```bash
uv sync                 # установить зависимости (Python 3.11)
uv run ruff check .     # линт
uv run ruff format --check .
uv run mypy .           # типизация
uv run pytest -q        # тесты
```

Запуск стека локально:

```bash
docker compose up -d --build
curl http://localhost:8000/health   # {"status":"ok"}
```
