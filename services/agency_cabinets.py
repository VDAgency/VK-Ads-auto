"""Заведение клиенту рекламного кабинета VK через агентский доступ (B2/B3).

План: docs/superpowers/plans/2026-08-25-agency-cabinets.md, волна B. Единая
операция `create_client_cabinet`: завести клиента у агентства VK
(`AgencyCabinetAdapter.create_agency_client`) → выпустить на него токен без
подтверждения клиента (`integrations.vk_oauth.request_agency_client_token`,
`grant_type=agency_client_credentials`) → сохранить как `AdAccount`,
закреплённый за клиентом (`services.ad_accounts.add_account`).

Модуль не знает про бриф/парсер брифа (`services.brief_parser`,
`services.briefs`) — по ним параллельно идёт другая задача (обязательность
ИНН). Имя рекламодателя (`full_name`) и его ИНН (`tax_id`) приходят уже
готовыми параметрами: их извлечение из брифа и подтверждение оператором —
дело вызывающего кода (карточка подтверждения, волна C плана).

Кто вызывает `/agency/clients.json` от имени агентства: пара ключей
приложения `vk_ads_client_id`/`vk_ads_client_secret` (те же, что выпускают
токены на кабинеты клиентов), причём выпуск СВОЕГО токена — тоже штатный
запрос, `integrations.vk_oauth.request_own_account_token`
(`grant_type=client_credentials`, «Client Credentials Grant — доступ к данным
собственного аккаунта» в документации VK). Это правка после ревью: первая
версия брала для этого `Settings.vk_ads_access_token` — тот же токен, что
бутстрапит «общий» кабинет оператора при посеве (`services.ad_accounts.
seed_from_env`), — рассуждая, что раз агентский статус получен на собственный
кабинет Анастасии, его токен и годится. Формально верно, но по сути это
возврат ручного шага: токен из окружения кто-то должен сначала добыть в
интерфейсе VK и вписать в `.env`, а раз он долгоживущий — рано или поздно
протухнет молча. Именно ручные шаги на нового клиента эта фича и убирает, так
что решение отклонено: токен собственного аккаунта теперь получается внутри
самой операции (`_build_agency_adapter`) и нигде не хранится дольше одного
вызова `create_client_cabinet` — ни в переменной модуля, ни в БД, ни в
`.env`. `Settings.vk_ads_access_token` при этом не тронут: он по-прежнему
нужен `seed_from_env` для разового посева уже существующего кабинета — это
отдельная история, не про заведение новых клиентских.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from config.settings import Settings, get_settings
from db.repositories import get_brief, get_client, list_ad_accounts_for_client
from integrations.adapter import AgencyCabinetAdapter
from integrations.vk_api import VkAgencyClientUnavailable, VkApiAdapter
from integrations.vk_oauth import (
    VkOAuthError,
    VkOAuthNotConfigured,
    VkOAuthUnavailable,
    request_agency_client_token,
    request_own_account_token,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.ad_accounts import (
    ADVERTISER_THIRD_PARTY,
    AdAccountView,
    ClientNotFoundError,
    DuplicateAccountError,
    add_account,
)
from services.secret_box import NotConfiguredError, SecretBox
from services.vk_identity import InvalidTokenError, VkUnreachableError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AgencyCabinetError(Exception):
    """Базовая ошибка заведения клиенту рекламного кабинета VK (B2/B3)."""


class AgencyDisabledError(AgencyCabinetError):
    """Предохранитель `vk_agency_confirmed` выключен (CLAUDE.md §1.4): боевое
    создание кабинетов VK не выполняется, пока агентский статус не подтверждён.
    """


class AgencyMissingTaxIdError(AgencyCabinetError):
    """ИНН не указан — без него кабинет не заводим: закон о рекламе требует
    указывать конечного рекламодателя (план 2026-08-25, задача A2)."""


class AgencyTokenIssuanceFailedError(AgencyCabinetError):
    """Клиент уже заведён в VK (`vk_client_id`/`vk_username` существуют), но
    выпустить на него токен не удалось — половинчатое состояние. Причина в
    `__cause__`; `vk_client_id`/`vk_username` даны, чтобы повторная попытка
    выпуска токена могла сослаться на уже созданного клиента, не заводя в VK
    дубль.
    """

    def __init__(self, vk_client_id: str, vk_username: str | None) -> None:
        self.vk_client_id = vk_client_id
        self.vk_username = vk_username
        super().__init__(f"VK client {vk_client_id} was created but token issuance failed")


class AgencyCabinetPersistError(AgencyCabinetError):
    """Клиент заведён и токен выпущен, но сохранить кабинет как `AdAccount` не
    удалось — половинчатое состояние. Причина в `__cause__`; `vk_client_id`/
    `vk_username` — тот же смысл, что у `AgencyTokenIssuanceFailedError`: за
    что зацепиться оператору, чтобы найти и прибрать уже созданного в VK
    клиента (токен, который не удалось сохранить, нигде не оседает —
    повторный запуск операции выпустит новый, в пределах лимита VK на пять
    живых токенов клиента).

    Базовый класс для этой группы отказов; конкретную причину смотрите либо
    в `__cause__`, либо по подклассу — `AgencyCabinetDuplicateError` и
    `AgencyCabinetClientGoneError` ниже разведены отдельно, потому что от них
    ожидаются разные действия оператора. Сюда, необёрнутым базовым классом,
    попадают все остальные причины: VK не подтвердил свежевыпущенный токен
    живым запросом (`InvalidTokenError`/`VkUnreachableError`), пропал ключ
    шифрования на середине операции (`NotConfiguredError`) — и, начиная с
    ревью ветки §2, вообще любой другой необработанный отказ этого шага
    (например, `IntegrityError` от гонки двух параллельных созданий: проверка
    дубля и вставка в `add_account` не атомарны). Перехват здесь намеренно
    исчерпывающий: важно не что именно сломалось при сохранении, а то, что
    клиент в VK и токен уже существуют и их номер нельзя потерять — тип
    причины не входит в число случаев, требующих особого действия оператора,
    и потому не разводится отдельным подклассом.
    """

    def __init__(self, vk_client_id: str, vk_username: str | None) -> None:
        self.vk_client_id = vk_client_id
        self.vk_username = vk_username
        super().__init__(f"VK client {vk_client_id} cabinet could not be persisted")


class AgencyCabinetDuplicateError(AgencyCabinetPersistError):
    """Тот же половинчатый смысл, что у `AgencyCabinetPersistError`, но
    причина конкретна: `add_account` отклонил сохранение, потому что кабинет
    с таким внешним id VK уже есть у тенанта (`DuplicateAccountError` в
    `__cause__`) — совпадение либо гонка двух параллельных созданий. Не
    «клиент пропал»: оператору нужно найти существующий дубль кабинета и
    решить вручную, что делать со свежесозданным клиентом/токеном в VK —
    другое действие, чем при `AgencyCabinetClientGoneError`.
    """


class AgencyCabinetClientGoneError(AgencyCabinetPersistError):
    """Тот же половинчатый смысл, что у `AgencyCabinetPersistError`, но
    причина конкретна: клиент, для которого заводили кабинет, исчез между
    ранней проверкой (`get_client` в начале операции) и сохранением —
    `add_account` отклонил его отсутствием (`ClientNotFoundError` в
    `__cause__`). Клиент и токен в VK уже созданы и осиротели: привязывать
    их больше не к кому, а найденный `vk_client_id`/`vk_username` — то немногое,
    за что оператор может зацепиться, чтобы прибрать их вручную.
    """


async def _retry_once(operation: Callable[[], Awaitable[T]], *, retry_on: type[Exception]) -> T:
    """Ровно одна повторная попытка при моргнувшей сети (план §3 «Две попытки»).

    Отказ по существу (нет прав, ошибка валидации — любой другой тип
    исключения) здесь не перехватывается и улетает с первой попытки: повторять
    его бессмысленно.
    """
    try:
        return await operation()
    except retry_on:
        logger.warning("VK request failed, retrying once: %s", retry_on.__name__)
        return await operation()


async def _build_agency_adapter(oauth_client_id: str, oauth_client_secret: str) -> VkApiAdapter:
    """Собственный токен агентства (`grant_type=client_credentials`,
    `integrations.vk_oauth.request_own_account_token`) — только на время этой
    операции. Никакого долгоживущего токена в окружении: ключи приложения уже
    настроены (`vk_ads_client_id`/`vk_ads_client_secret`), выпуск занимает один
    запрос и не требует ручного шага. Токен нигде не кэшируется дольше одного
    вызова `create_client_cabinet` — вызывающая функция строит адаптер один раз
    и переиспользует его инстанс на обе свои сетевые попытки (создание клиента
    может сработать с первой или со второй попытки, токен для обеих один и тот
    же), но не хранит его после возврата.
    """
    own_token = await _retry_once(
        lambda: request_own_account_token(oauth_client_id, oauth_client_secret),
        retry_on=VkOAuthUnavailable,
    )
    return VkApiAdapter(own_token.access_token)


def _cabinet_name(full_name: str, niche: str | None) -> str:
    """Имя кабинета для VK: ФИО (или название компании) — основа; ниша
    дописывается, только если она известна (решение заказчика, план §4).

    Автоматическое определение ниши по категории сообщества VK сюда сознательно
    НЕ добавлено (см. отчёт задачи B2/B3): для этого понадобился бы отдельный
    токен другой, не рекламной, VK API (Groups API, другой хост и другое
    приложение) — новая зависимость и новый секрет, которых план явно просит не
    заводить, если чисто не получается. Без ниши имя остаётся из одного ФИО —
    решение допускает это как нормальный исход, схема не ломается.
    """
    name = full_name.strip()
    trimmed_niche = niche.strip() if niche else ""
    return f"{name} — {trimmed_niche}" if trimmed_niche else name


async def create_client_cabinet(
    session: AsyncSession,
    account_id: int,
    client_id: int,
    *,
    full_name: str,
    tax_id: str | None,
    niche: str | None = None,
    settings: Settings | None = None,
    agency_adapter: AgencyCabinetAdapter | None = None,
) -> AdAccountView:
    """Завести клиенту рекламный кабинет VK: клиент агентства → токен → `AdAccount`.

    Единая операция (B2): `agency_adapter.create_agency_client` заводит клиента
    у агентства → `request_agency_client_token` выпускает на него токен без
    подтверждения клиента (`grant_type=agency_client_credentials`) →
    `services.ad_accounts.add_account` проверяет токен живым запросом,
    шифрует и сохраняет `AdAccount` с проставленным `client_id` и
    `advertiser_kind=third_party` (конечный рекламодатель — сам клиент, не
    оператор: `advertiser_name`/`advertiser_inn` заполняются из `full_name`/
    `tax_id`).

    Предохранители, оба обязательны: `settings.vk_agency_confirmed` (иначе
    `AgencyDisabledError`) и непустой `tax_id` (иначе `AgencyMissingTaxIdError`)
    — обе проверки идут ДО первого сетевого вызова, вместе с проверкой, что
    `client_id` существует у тенанта (`ClientNotFoundError`), что настроен
    ключ шифрования (`NotConfiguredError`) и OAuth-приложение агентства
    (`VkOAuthNotConfigured`) — чтобы не тратить операцию VK впустую там, где
    заведомо нечем будет сохранить результат.

    Сеть: у выпуска собственного токена агентства, у создания клиента и у
    выпуска токена клиенту — по одной повторной попытке при
    `VkOAuthUnavailable`/`VkAgencyClientUnavailable` (план §3, «ровно две
    попытки, не больше»); отказ по существу (`VkOAuthNotConfigured`,
    `VkOAuthInvalidCredentials`, `VkOAuthRejected`, `VkAgencyClientForbidden`,
    `VkAgencyClientValidationError`, …) не повторяется и улетает наверх как
    есть — это типизированные исключения `integrations.vk_oauth`/
    `integrations.vk_api`, ловите их отдельно там, где нужен свой текст для
    оператора. `VkOAuthNotConfigured` за пустые `vk_ads_client_id`/
    `vk_ads_client_secret` в любом случае бросается до сети — см. проверку
    ниже, до выпуска собственного токена.

    ⚠️ Повтор `adapter.create_agency_client` на `VkAgencyClientUnavailable`
    неидемпотентен (ревью ветки §5): `VkAgencyClientUnavailable` покрывает и
    «сеть точно не дошла», и «дошла, VK создал клиента, но ответ потерялся
    или пришёл битым» — во втором случае повтор заводит второго клиента в
    VK, а этот метод такого не различает. Дедупликация через
    `VkApiAdapter.list_agency_clients` сюда сознательно НЕ подключена — см. её
    докстринг (`integrations/vk_api.py`), почему это решение отложено до
    боевой проверки агентского доступа, а не реализовано вслепую. Самый
    опасный подслучай ничем не подсвечен: если повтор из-за такого потерянного
    ответа тихо завёл второго клиента, а дальше выпуск токена и сохранение
    всё же прошли успешно, метод вернётся обычным успехом — исключения не
    будет вовсе, и `vk_client_id`/`vk_username` осиротевшего первого клиента
    из абзаца ниже про половинчатые состояния тут не помогут: та защита
    срабатывает только когда операция падает ПОСЛЕ дубля, а не когда повтор
    молча доходит до конца. Опознать такой дубль сейчас может только боевая
    проверка (сверка с `list_agency_clients` вручную), не код.

    ⚠️ Контракт `AgencyCabinetAdapter.create_agency_client` (через
    `VkApiAdapter`) сверен с документацией VK, но боевым вызовом ЕЩЁ НЕ
    проверен — `vk_agency_confirmed` выключен, живого теста не было.

    Половинчатые состояния — «клиент в VK уже есть, а кабинета у нас нет» —
    не имитируются успехом: `AgencyTokenIssuanceFailedError` (клиент заведён,
    токен не выпущен) и вся семья `AgencyCabinetPersistError` (токен выпущен,
    но сохранить не удалось: `AgencyCabinetDuplicateError` — совпал внешний
    id VK с уже активным кабинетом тенанта; `AgencyCabinetClientGoneError` —
    клиент исчез между ранней проверкой и сохранением; базовый класс — любая
    другая причина на этом шаге) несут `vk_client_id`/`vk_username`, но
    никогда сам токен — он никуда, кроме зашифрованной колонки, не попадает.

    `agency_adapter` — для тестов и для явного выбора площадки; по умолчанию
    строится `VkApiAdapter` на собственном токене агентства, выпущенном на
    время операции (`_build_agency_adapter`, `grant_type=client_credentials`)
    — см. модульную документацию, почему не токен из окружения.
    """
    cfg = settings or get_settings()

    if not cfg.vk_agency_confirmed:
        raise AgencyDisabledError(
            "vk_agency_confirmed is off: live VK agency cabinet creation is disabled"
        )
    cleaned_tax_id = (tax_id or "").strip()
    if not cleaned_tax_id:
        raise AgencyMissingTaxIdError("tax_id (ИНН) is required to create a client cabinet")

    if await get_client(session, account_id, client_id) is None:
        raise ClientNotFoundError(str(client_id))

    box = SecretBox(cfg.vk_ads_secret_key.get_secret_value())
    if not box.configured:
        raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")

    oauth_client_id = cfg.vk_ads_client_id.get_secret_value()
    oauth_client_secret = cfg.vk_ads_client_secret.get_secret_value()
    if not oauth_client_id.strip() or not oauth_client_secret.strip():
        raise VkOAuthNotConfigured("vk_ads_client_id/vk_ads_client_secret are not configured")

    adapter = agency_adapter or await _build_agency_adapter(oauth_client_id, oauth_client_secret)
    name = _cabinet_name(full_name, niche)

    vk_client = await _retry_once(
        lambda: adapter.create_agency_client(client_name=name, client_info=f"ИНН {cleaned_tax_id}"),
        retry_on=VkAgencyClientUnavailable,
    )
    logger.info(
        "VK agency client created: vk_client_id=%s username=%s",
        vk_client.client_id,
        vk_client.username,
    )

    try:
        token = await _retry_once(
            lambda: request_agency_client_token(
                oauth_client_id,
                oauth_client_secret,
                agency_client_name=vk_client.username or None,
                agency_client_id=None if vk_client.username else vk_client.client_id,
            ),
            retry_on=VkOAuthUnavailable,
        )
    except VkOAuthError as exc:
        raise AgencyTokenIssuanceFailedError(vk_client.client_id, vk_client.username) from exc

    try:
        view = await add_account(
            session,
            account_id,
            token.access_token.get_secret_value(),
            title=name,
            refresh_token=token.refresh_token.get_secret_value(),
            advertiser_kind=ADVERTISER_THIRD_PARTY,
            advertiser_name=full_name.strip(),
            advertiser_inn=cleaned_tax_id,
            client_id=client_id,
            settings=cfg,
        )
    except DuplicateAccountError as exc:
        # Совпал внешний id VK с уже активным кабинетом тенанта — не то же
        # самое, что «клиент пропал» (ниже): другой текст, другое действие
        # оператора, поэтому отдельный подкласс, а не общий персист-отказ.
        raise AgencyCabinetDuplicateError(vk_client.client_id, vk_client.username) from exc
    except ClientNotFoundError as exc:
        # Клиент исчез между ранней проверкой (в начале функции) и этим
        # сохранением — узкое окно (гонка/удаление), но `add_account` умеет
        # его обнаружить сам через свою собственную проверку.
        raise AgencyCabinetClientGoneError(vk_client.client_id, vk_client.username) from exc
    except (InvalidTokenError, VkUnreachableError, NotConfiguredError) as exc:
        raise AgencyCabinetPersistError(vk_client.client_id, vk_client.username) from exc
    except Exception as exc:  # noqa: BLE001 — полусостояние: класть причину, а не тип
        # Исчерпывающий перехват намеренно (ревью ветки §2): проверка дубля и
        # вставка в `add_account` не атомарны (частичный уникальный индекс на
        # таблице), поэтому настоящая гонка двух параллельных созданий может
        # дать сырой `IntegrityError` вместо `DuplicateAccountError` — и любой
        # другой сбой БД на этом шаге тоже. Ловить типы по одному здесь
        # бессмысленно: важно не что именно сломалось при сохранении, а то,
        # что клиент в VK и токен уже существуют и их номер нельзя терять —
        # значит любой необработанный отказ этого шага обязан остаться
        # `AgencyCabinetPersistError` с `vk_client_id`/`vk_username`, а не
        # голым исключением, которое роутер не опознает и бот покажет общим
        # текстом без номера клиента VK.
        raise AgencyCabinetPersistError(vk_client.client_id, vk_client.username) from exc

    logger.info(
        "client cabinet created: ad_account_id=%s client_id=%s vk_client_id=%s",
        view.id,
        client_id,
        vk_client.client_id,
    )
    return view


# Ключ брифа, из которого берётся ИНН клиента (services/brief_fields.py:
# оба варианта брифа — individual и community — используют один и тот же ключ
# payload, только разные подписи поля: "ИНН" / "ИНН / ОГРН / ОГРНИП").
_TAX_ID_PAYLOAD_KEY = "tax_id"


@dataclass(frozen=True, slots=True)
class CabinetStepState:
    """Состояние шага C1 (план 2026-08-25-agency-cabinets, волна C) — что каналу
    показать перед выбором кабинета, без чтения настроек и без разбора брифа
    самим каналом (CLAUDE.md §1.3): решает ядро, каналы только показывают.

    `available=False` — шага нет вовсе, не «есть, но заблокирован»:
    `vk_agency_confirmed` выключен, брифа нет у тенанта либо у брифа нет
    привязанного клиента (заводить кабинет решительно не для кого).

    `blocked_reason` заполнен, только когда шаг доступен и своего кабинета у
    клиента ещё нет, но чего-то не хватает для предложения создания:
    `"missing_name"` (не указано имя/название клиента) или `"missing_tax_id"`
    (не указан ИНН). Вызывающая сторона в этом случае показывает предупреждение
    и продолжает обычным выбором кабинета — поток не блокируется.
    """

    available: bool
    own_cabinet_exists: bool
    blocked_reason: str | None = None


async def cabinet_step_state(
    session: AsyncSession, account_id: int, brief_id: int, *, settings: Settings | None = None
) -> CabinetStepState:
    """Предусловия шага C1 по брифу — единственное место, где они решаются.

    Раньше это была пара функций бота (`_own_cabinet_missing`,
    `_cabinet_prereq_issue`) плюс прямое чтение `get_settings().vk_agency_confirmed`
    в обработчике (`bot/handlers/creative.py`) — канал не должен читать настройки
    ядра и разбирать бриф сам. Сама агентская схема создания кабинета
    (`create_client_cabinet` выше) этой функцией не затрагивается: здесь только
    чтение предусловий рядом с ней.
    """
    cfg = settings or get_settings()
    if not cfg.vk_agency_confirmed:
        return CabinetStepState(available=False, own_cabinet_exists=False)

    brief = await get_brief(session, account_id, brief_id)
    if brief is None or brief.client_id is None:
        return CabinetStepState(available=False, own_cabinet_exists=False)

    accounts = await list_ad_accounts_for_client(session, account_id, brief.client_id)
    own_cabinet_exists = any(row.client_id == brief.client_id for row in accounts)
    if own_cabinet_exists:
        return CabinetStepState(available=True, own_cabinet_exists=True)

    client = await get_client(session, account_id, brief.client_id)
    full_name = (client.full_name if client else None) or ""
    if not full_name.strip():
        return CabinetStepState(
            available=True, own_cabinet_exists=False, blocked_reason="missing_name"
        )

    tax_id = str(brief.payload.get(_TAX_ID_PAYLOAD_KEY) or "")
    if not tax_id.strip():
        return CabinetStepState(
            available=True, own_cabinet_exists=False, blocked_reason="missing_tax_id"
        )

    return CabinetStepState(available=True, own_cabinet_exists=False)


__all__ = [
    "AgencyCabinetClientGoneError",
    "AgencyCabinetDuplicateError",
    "AgencyCabinetError",
    "AgencyCabinetPersistError",
    "AgencyDisabledError",
    "AgencyMissingTaxIdError",
    "AgencyTokenIssuanceFailedError",
    "CabinetStepState",
    "cabinet_step_state",
    "create_client_cabinet",
]
