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
CMD ["uv", "run", "uvicorn", "core.app:app", "--host", "0.0.0.0", "--port", "8000"]
