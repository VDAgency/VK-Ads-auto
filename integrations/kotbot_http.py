"""KotbotAdapter — канал kotbot как тонкий HTTP-клиент к сервису `kotbot/`.

Ядро не знает про Playwright: браузерная автоматизация живёт в отдельном сервисе
(`kotbot/`, свой контейнер), а адаптер только ходит по его HTTP-контракту
(спека 2026-07-17 §3, §4.3, §7). Маршруты и формы запросов зафиксированы спекой —
менять их можно только вместе с сервисом.

Карта ошибок (спека §4.2/§4.3):
- 409 `reauth_required` → `KotbotReauthRequired`: сессия протухла, нужен оператор
  (`/link_kotbot`). Сервис сам не ретраит вход — защита от блокировки аккаунта;
- 502 `flow_failed:<шаг>` → `KotbotFlowFailed` с именем упавшего шага флоу;
- прочие не-2xx (в т.ч. 501, пока живые флоу не написаны) и транспорт →
  `KotbotRequestError`. «Голый» httpx наружу не выпускаем: сервис запуска должен
  видеть отказ канала, а не деталь транспорта.

`health_check` ошибок не бросает — им пользуется `ChannelRouter` для выбора канала
и фолбэка (нездоров → кампания уходит на заглушку с честным сообщением оператору).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import asdict
from typing import Any

import httpx
from services.mapping import CampaignSpec

from integrations.adapter import PlatformAdapter

logger = logging.getLogger(__name__)

# Действие = браузерный флоу (навигация + мастер ads.vk.ru) — ждём долго (спека §3).
ACTION_TIMEOUT = httpx.Timeout(300.0)
# `/health` дешёвый (файлы + флаги в памяти) — роутеру каналов нельзя вставать надолго.
HEALTH_TIMEOUT = httpx.Timeout(5.0)

REAUTH_STATUS = 409
FLOW_FAILED_STATUS = 502
FLOW_FAILED_PREFIX = "flow_failed:"


class KotbotError(RuntimeError):
    """Базовая ошибка канала kotbot (сервис не выполнил действие)."""


class KotbotReauthRequired(KotbotError):
    """Сервис требует переавторизации оператором (409 `reauth_required`)."""

    def __init__(self) -> None:
        super().__init__("kotbot requires re-authorization: run /link_kotbot")


class KotbotFlowFailed(KotbotError):
    """Браузерный флоу упал на конкретном шаге (502 `flow_failed:<шаг>`)."""

    def __init__(self, step: str) -> None:
        super().__init__(f"kotbot flow failed at step: {step}")
        self.step = step


class KotbotRequestError(KotbotError):
    """Сервис недоступен или ответил ошибкой (транспорт, 501 и прочие не-2xx)."""


def _detail(response: httpx.Response) -> str:
    """Код ошибки из тела FastAPI (`{"detail": ...}`); пусто, если тело не JSON."""
    with contextlib.suppress(ValueError):
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("detail", ""))
    return ""


def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
    """Перевести ответ сервиса в исключение канала (или пропустить дальше 2xx)."""
    if response.status_code == REAUTH_STATUS:
        raise KotbotReauthRequired
    detail = _detail(response)
    if response.status_code == FLOW_FAILED_STATUS and detail.startswith(FLOW_FAILED_PREFIX):
        raise KotbotFlowFailed(detail[len(FLOW_FAILED_PREFIX) :])
    if response.status_code >= 400:
        raise KotbotRequestError(
            f"kotbot {response.status_code} on {method} {path}: {detail or 'no detail'}"
        )


def _as_float(value: Any) -> float | None:
    """Метрика в float; нечисловые значения (строки-подписи, None) отбрасываем."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class KotbotAdapter(PlatformAdapter):
    """Адаптер канала kotbot. `client` можно подменить (тесты/общий пул соединений)."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        request_timeout: httpx.Timeout = ACTION_TIMEOUT,
    ) -> httpx.Response:
        """Один запрос к сервису; транспортную ошибку заворачиваем в ошибку канала."""
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                return await self._client.request(method, url, json=json, timeout=request_timeout)
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                return await client.request(method, url, json=json)
        except httpx.HTTPError as exc:
            raise KotbotRequestError(f"kotbot is unreachable on {method} {path}: {exc}") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Действие сервиса: запрос → карта ошибок → тело-объект."""
        response = await self._send(method, path, json=json)
        _raise_for_status(response, method, path)
        try:
            payload = response.json()
        except ValueError as exc:
            raise KotbotRequestError(f"kotbot returned non-JSON body on {method} {path}") from exc
        if not isinstance(payload, dict):
            raise KotbotRequestError(f"kotbot returned unexpected body on {method} {path}")
        return payload

    async def health_check(self) -> bool:
        """`GET /health` (5с): здоров = сервис отвечает и есть живая стратегия входа.

        Ошибок не бросаем — это опрос роутера каналов: любая проблема означает
        «канал не годится», решение о фолбэке принимает вызывающий.
        """
        try:
            response = await self._send("GET", "/health", request_timeout=HEALTH_TIMEOUT)
        except KotbotError as exc:
            logger.warning("kotbot health check failed: %s", exc)
            return False
        if response.status_code != 200:
            logger.warning("kotbot health check returned %s", response.status_code)
            return False
        try:
            payload = response.json()
        except ValueError:
            logger.warning("kotbot health check returned non-JSON body")
            return False
        return bool(payload.get("healthy", False)) if isinstance(payload, dict) else False

    async def create_cabinet(
        self,
        account_id: int,
        client_ref: str,
        *,
        ad_object_url: str | None = None,
        ad_object_name: str | None = None,
    ) -> str:
        """`POST /cabinets` — завести кабинет под объект рекламы; вернуть внешний ref."""
        body: dict[str, Any] = {"client_ref": client_ref, "ad_object_url": ad_object_url or ""}
        if ad_object_name is not None:
            body["ad_object_name"] = ad_object_name
        payload = await self._request("POST", "/cabinets", json=body)
        return str(payload["external_ref"])

    async def create_campaign(
        self, cabinet_id: str, goal: str, *, spec: CampaignSpec | None = None
    ) -> str:
        """`POST /campaigns` — создать кампанию по спеке; вернуть её внешний id."""
        return await self._create_campaign(cabinet_id, self._spec_body(goal, spec))

    async def create_campaign_from_spec(
        self,
        cabinet_id: str,
        spec: CampaignSpec,
        *,
        creative_ref: str | None = None,
        title: str | None = None,
        body: str | None = None,
        budget_limit_day: float | None = None,
    ) -> str:
        """Кампания целиком одним вызовом ядра: спека + креатив + тексты.

        Ядро не разбирает кампанию на шаги площадки (CLAUDE.md §1.3): оно отдаёт
        всё сюда, а адаптер раскладывает это по маршрутам сервиса (спека §4.3) —
        `POST /campaigns` со спекой-словарём и, при наличии медиа,
        `POST /campaigns/{ext}/creative` с файлом и текстами.
        """
        spec_body = self._spec_body(spec.objective, spec)
        if budget_limit_day is not None:
            # Дневной лимит считает сервис запуска (`daily_budget_rub`) — площадке
            # он нужен готовым числом, поэтому едет вместе со спекой.
            spec_body["spec"]["budget_limit_day"] = float(budget_limit_day)
        campaign_id = await self._create_campaign(cabinet_id, spec_body)
        if creative_ref:
            await self.upload_creative(campaign_id, creative_ref, title=title, body=body)
        return campaign_id

    async def upload_creative(
        self,
        campaign_id: str,
        creative_ref: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> str:
        """`POST /campaigns/{ext}/creative` — медиа (путь в томе creatives) и тексты."""
        payload = await self._request(
            "POST",
            f"/campaigns/{campaign_id}/creative",
            json={"file_path": creative_ref, "title": title, "body": body},
        )
        return str(payload["creative_ref"])

    async def launch(self, campaign_id: str) -> None:
        """`POST /campaigns/{ext}/launch` — запустить кампанию (дальше модерация VK)."""
        await self._request("POST", f"/campaigns/{campaign_id}/launch")

    async def stop(self, campaign_id: str) -> None:
        """`POST /campaigns/{ext}/stop` — остановить кампанию."""
        await self._request("POST", f"/campaigns/{campaign_id}/stop")

    async def get_status(self, campaign_id: str) -> str:
        """`GET /campaigns/{ext}/status` — статус кампании; без поля — `unknown`."""
        payload = await self._request("GET", f"/campaigns/{campaign_id}/status")
        status = payload.get("status")
        return str(status) if status else "unknown"

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        """`GET /campaigns/{ext}/stats` — показы/клики/расход/результаты числами."""
        payload = await self._request("GET", f"/campaigns/{campaign_id}/stats")
        stats: dict[str, float] = {}
        for key, raw in payload.items():
            value = _as_float(raw)
            if value is not None:
                stats[str(key)] = value
        return stats

    async def _create_campaign(self, cabinet_id: str, spec_body: dict[str, Any]) -> str:
        """Общая часть создания кампании: тело со спекой → внешний id кампании."""
        payload = await self._request(
            "POST", "/campaigns", json={"cabinet_ref": cabinet_id, **spec_body}
        )
        return str(payload["external_id"])

    @staticmethod
    def _spec_body(goal: str, spec: CampaignSpec | None) -> dict[str, Any]:
        """Спека для сервиса словарём; без спеки — минимальная форма с целью."""
        return {"spec": asdict(spec) if spec is not None else {"objective": goal}}
