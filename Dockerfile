# Сборка фронта Блока 2: Next.js static export → /web/out. Отдельная стадия, чтобы
# в финальном образе не осталось ни Node, ни node_modules — только готовые файлы.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# --include=dev обязателен: next build требует typescript и eslint-config-next,
# которые лежат в devDependencies. Если в окружении сборки окажется
# NODE_ENV=production, npm молча выставит omit=dev и сборка упадёт на типах.
RUN npm ci --include=dev
COPY web/ ./
RUN npm run build

# Образ ядра (FastAPI) и бота. Зависимости ставятся через uv.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

# ffmpeg — для сборки статичного ролика из присланной клиентом картинки
# (integrations/vk_video.py). Отдельный микросервис под это не заводим.
RUN apt-get update     && apt-get install -y --no-install-recommends ffmpeg     && rm -rf /var/lib/apt/lists/*

# uv из официального образа.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Сначала зависимости (кешируемый слой), затем код.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Строго ПОСЛЕ `COPY . .`: иначе исходники из контекста затрут собранный фронт.
COPY --from=web /web/out /app/web/out

# Непривилегированный пользователь (аудит 2026-09-01, CKV_DOCKER_3). UID фиксирован:
# владельца на томах выставляет другой контейнер (init-permissions в compose), и
# совпасть они обязаны по номеру, а не по имени.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/creatives /home/app/.cache/uv \
    && chown -R app:app /app /data/creatives /home/app

ENV UV_CACHE_DIR=/home/app/.cache/uv

USER app

EXPOSE 8000

# По умолчанию запускается ядро; бот переопределяет command в docker-compose.
# --proxy-headers: перед ядром стоит Caddy, настоящий IP клиента приходит в
# X-Forwarded-For. Без флага rate-limit ключуется по IP прокси и становится общим
# на всех посетителей сразу (ложные 429).
# --forwarded-allow-ips="*" допустим только потому, что порт 8000 публикуется на
# loopback (docker-compose.yml): подделать заголовок может лишь тот, кто уже внутри
# compose-сети или на самом сервере.
# --no-sync: под непривилегированным пользователем `uv run` не может писать в
# окружение проекта; без флага он попытается пересинхронизировать его при старте.
CMD ["uv", "run", "--no-sync", "uvicorn", "core.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
