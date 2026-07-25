"""Фабрика FastAPI-приложения — единая точка сборки headless-ядра.

Вся бизнес-логика живёт в `core`/`services`; каналы (бот, веб) — тонкие клиенты
поверх внутреннего API. Здесь только сборка приложения и подключение роутеров.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from services.notifier_telegram import register_telegram_notifier
from starlette.responses import Response
from starlette.types import Scope

from core.api import health
from core.api.v1 import router as v1_router

# Каталог собранного фронта Блока 2 (Next.js static export). Корень репозитория /
# web / out. Наполняется `npm run build`; в образ кладётся отдельной стадией
# Dockerfile. Локально появляется после `cd web && npm run build`.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "out"


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles с `Cache-Control: no-cache` и фолбэком на `.html`.

    `no-cache` — браузер всегда перепроверяет свежесть. Формы/JS меняются при
    деплое; без него браузер может отдавать старый JS (например, форму брифа без
    токена `?t=`, из-за чего инвайт не метится received). ETag/Last-Modified
    делают перепроверку дешёвой (304, если файл не менялся).

    Фолбэк `.html` — следствие статического экспорта Next: роут `/cabinet`
    экспортируется в файл `cabinet.html`. Разосланные клиентам ссылки ведут на
    `/cabinet.html?token=...`, внутренняя навигация Next — на `/cabinet`; оба
    пути обязаны отдавать один и тот же файл.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        if not Path(path).suffix:
            _, stat_result = self.lookup_path(f"{path}.html")
            if stat_result is not None:
                path = f"{path}.html"
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Старт процесса ядра: подключить транспорты, живущие в процессе api."""
    # Регистрируем транспорт уведомлений оператору (см. services/notifier_telegram.py):
    # POST /briefs обрабатывается здесь, в процессе api, а бот — отдельный процесс.
    register_telegram_notifier()
    yield


def create_app() -> FastAPI:
    """Собрать и вернуть экземпляр FastAPI-приложения."""
    app = FastAPI(title="VK-Ads-auto", version="0.0.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(v1_router.router)
    # Статику монтируем ПОСЛЕ API — /health и /api/v1 имеют приоритет.
    if _STATIC_DIR.is_dir():
        app.mount("/", _NoCacheStaticFiles(directory=_STATIC_DIR, html=True), name="web")
    return app


app = create_app()
