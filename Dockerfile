# Образ ядра (FastAPI) и бота. Зависимости ставятся через uv.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

# uv из официального образа.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Сначала зависимости (кешируемый слой), затем код.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000

# По умолчанию запускается ядро; бот переопределяет command в docker-compose.
# --proxy-headers: перед ядром стоит Caddy, настоящий IP клиента приходит в
# X-Forwarded-For. Без флага rate-limit ключуется по IP прокси и становится общим
# на всех посетителей сразу (ложные 429).
# --forwarded-allow-ips="*" допустим только потому, что порт 8000 публикуется на
# loopback (docker-compose.yml): подделать заголовок может лишь тот, кто уже внутри
# compose-сети или на самом сервере.
CMD ["uv", "run", "uvicorn", "core.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
