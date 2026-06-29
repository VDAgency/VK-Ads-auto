"""Healthcheck-эндпоинт ядра. Используется деплоем для проверки живости."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Вернуть статус живости сервиса."""
    return {"status": "ok"}
