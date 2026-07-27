"""Фабрика FastAPI-приложения — единая точка сборки headless-ядра.

Вся бизнес-логика живёт в `core`/`services`; каналы (бот, веб) — тонкие клиенты
поверх внутреннего API. Здесь только сборка приложения и подключение роутеров.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
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


class _WebStaticFiles(StaticFiles):
    """StaticFiles с кэш-политикой по типу файла и фолбэком на `.html`.

    HTML — `no-cache`: браузер всегда перепроверяет свежесть. Без этого он
    отдавал старую форму брифа без токена `?t=`, из-за чего инвайт не метился
    received. ETag/Last-Modified делают перепроверку дешёвой (304, если файл
    не менялся).

    Ассеты Next под `/_next/static/` несут хеш содержимого в имени, поэтому
    кешируются надолго: изменилось содержимое — изменилось имя файла. Прежний
    сплошной `no-cache` заставлял браузер перепроверять около двадцати файлов
    ассетов при каждом заходе.

    Фолбэк `.html` — следствие статического экспорта Next: роут `/cabinet`
    экспортируется в файл `cabinet.html`. Разосланные клиентам ссылки ведут на
    `/cabinet.html?token=...`, внутренняя навигация Next — на `/cabinet`; оба
    пути обязаны отдавать один и тот же файл.
    """

    _IMMUTABLE_PREFIX = "/_next/static/"

    async def get_response(self, path: str, scope: Scope) -> Response:
        if not Path(path).suffix:
            _, stat_result = self.lookup_path(f"{path}.html")
            if stat_result is not None:
                path = f"{path}.html"
        response = await super().get_response(path, scope)
        request_path = scope.get("path", "")
        if isinstance(request_path, str) and request_path.startswith(self._IMMUTABLE_PREFIX):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
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
    # Сжатие текстовых ответов. StaticFiles ничего не сжимает сам, а фронт
    # Блока 2 — сотни килобайт JS и CSS: замер прод-сборки дал 421 КБ лишнего
    # трафика на один визит лендинга (по всей сборке 736.7 → 208.0 КБ).
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.include_router(health.router)
    app.include_router(v1_router.router)
    # Статику монтируем ПОСЛЕ API — /health и /api/v1 имеют приоритет.
    if _STATIC_DIR.is_dir():
        app.mount("/", _WebStaticFiles(directory=_STATIC_DIR, html=True), name="web")
    return app


app = create_app()
