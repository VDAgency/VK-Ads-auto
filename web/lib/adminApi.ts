// Тонкая обёртка над apiFetch для операторских эндпоинтов `/api/v1/admin/*`.
// Своей логики не несёт — только префикс, чтобы он не размазывался по экранам.

import { apiFetch, ApiError } from "@/lib/api";

export function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return apiFetch<T>(`/admin${path}`, init);
}

export const STATUS_RU: Record<string, string> = {
  received: "Принят",
  parsed: "В работе",
  prepared: "Готов к запуску",
  launched: "Запущена",
  moderation: "На модерации",
  stopped: "Остановлена",
  failed: "Ошибка",
  // Легаси-значения статуса кабинета из более старых записей (bot/handlers/stats.py
  // `_STATUS_HINT` держит их же ради обратной совместимости) — сегодняшние кампании
  // используют статусы выше, но старые строки в БД могли остаться с этими.
  active: "Активна",
  paused: "На паузе",
};

export const VARIANT_RU: Record<string, string> = {
  individual: "Физлицо",
  community: "Бизнес",
};

export type AdminMe = { operator_id: number };

export type Overview = {
  clients: number;
  pending: number;
  recent: number;
  campaigns: number;
};

export type ClientRow = {
  id: number;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  telegram: string | null;
  brief_count: number;
};

export type ClientBrief = { id: number; variant: string; status: string };

export type ClientDetail = {
  id: number;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  telegram: string | null;
  briefs: ClientBrief[];
};

export type BriefListItem = {
  contact: string;
  contact_name: string | null;
  variant: string;
  channel: string;
  waiting_days: number;
  brief_id: number | null;
};

/** Строка полного списка брифов (`status=all`) — включая пришедшие без
 * приглашения (реферальная ссылка клиента или холодный трафик с лендинга),
 * которых `pending`/`recent` не видят вовсе. */
export type BriefAllItem = {
  brief_id: number;
  variant: string;
  status: string;
  source: string;
  created_at: string;
  client_id: number | null;
  client_name: string | null;
};

export type CampaignRow = {
  id: number;
  brief_id: number;
  client_name: string | null;
  /** Кабинет, которым оплачена кампания — `null` у кампаний, заведённых до его появления. */
  ad_account_title: string | null;
  ad_account_external_id: string | null;
  status: string;
  objective: string;
};

export type BriefField = { n: number; label: string; value: string };

export type BriefCard = {
  brief_id: number;
  variant: string;
  status: string;
  client: {
    full_name: string | null;
    email: string | null;
    phone: string | null;
    telegram: string | null;
    /** Числовой id клиента брифа — им сужается список кабинетов (`?client_id=`)
     *  и подтверждается заведение кабинета клиенту. */
    id: number | null;
  };
  fields: BriefField[];
  has_creative: boolean;
  campaign_status?: string | null;
  /** Распознанная площадка подписки — считает ядро, здесь только показываем. */
  surface_title?: string;
  /** Нужен ли креатив: продвижение готового поста обходится без него. */
  surface_needs_creative?: boolean;
  /** Название цели запуска без креатива (`services.goals.NO_CREATIVE_GOAL`) —
   *  канал сам её не выбирает и не считает. */
  launch_goal_title?: string;
  /** Состояние шага «завести клиенту кабинет автоматически» — решает ядро,
   *  канал только показывает. `available=false` значит «шага нет вовсе». */
  cabinet_step_available?: boolean;
  cabinet_step_own_cabinet_exists?: boolean;
  cabinet_step_blocked_reason?: string | null;
  unknown?: number[];
};

export type InviteResult = {
  invite_id: number;
  status: string;
  channel: string;
  fallback_text: string | null;
  error: string | null;
};

/** Сообщение в общей полосе результата админки.
 *
 * `persistent` — для необратимых действий (запуск кампании, отправка креатива):
 * подтверждение остаётся на экране. Обратимые (правки полей) исчезают сами
 * (spec 2026-08-31 §7: «успех — короткое подтверждение, исчезающее само; для
 * необратимого — остаётся»). */
export type Flash = { text: string; ok: boolean; persistent?: boolean } | null;

/** Собрать контакты клиента в одну строку (как в прежней вёрстке). */
export function contactLine(client: {
  email: string | null;
  phone: string | null;
  telegram: string | null;
}): string {
  return [client.email, client.phone, client.telegram].filter(Boolean).join(" · ");
}

/** Рекламный кабинет оператора (зеркало `AdAccountOut` ядра, spec 2026-07-27 §8.3).
 *  Поля с токеном здесь нет и быть не может — только `token_tail`. */
export type AdAccount = {
  id: number;
  title: string;
  external_id: string;
  username: string | null;
  token_tail: string;
  advertiser_kind: string;
  advertiser_name: string | null;
  advertiser_inn: string | null;
  /** Клиент, за которым закреплён кабинет; `null` — кабинет общий. */
  client_id: number | null;
  client_name: string | null;
  status: string;
  health: string;
  health_checked_at: string | null;
  health_error: string | null;
  balance_rub: string | null;
  is_usable: boolean;
};

/** Человеческие подписи состояний health-check (те же, что в боте).
 *
 * `ok` — не код из `services.ad_accounts` (там всего четыре: `healthy`/
 * `unauthorized`/`error`/`unknown`), но встречается в данных как синоним
 * «жив» — без записи здесь бейдж показывал бы сырой код `ok` оператору
 * (spec 2026-08-31 §«Починить в списке кабинетов»). */
export const HEALTH_RU: Record<string, string> = {
  healthy: "✅ жив",
  ok: "✅ жив",
  unauthorized: "⛔ токен не принят",
  error: "⚠️ VK не ответил",
  unknown: "… не проверялся",
};

/** Состояния health-check, при которых `health_error` действительно относится
 * к ТЕКУЩЕЙ проверке. Для остальных состояний (включая неизвестные/легаси
 * значения вроде `ok`) поле может хранить текст ПРОШЛОЙ неудачной проверки —
 * ядро не гарантирует, что очистит его при следующем успешном прогоне из
 * старых записей, поэтому фронт обязан сам не показывать чужую по времени
 * ошибку рядом с сегодняшним «всё хорошо» (найденный баг, spec 2026-08-31). */
const BAD_HEALTH = new Set(["unauthorized", "error"]);

export function isHealthBad(health: string): boolean {
  return BAD_HEALTH.has(health);
}

/** Причины отказа добавления кабинета — по коду `detail` из ядра. */
export const AD_ACCOUNT_ERRORS: Record<string, string> = {
  invalid_token: "VK не принял этот токен. Проверьте, что скопирован весь access_token.",
  duplicate_account: "Такой кабинет уже добавлен.",
  vk_unreachable: "VK сейчас не отвечает. Попробуйте ещё раз через минуту.",
  encryption_key_missing:
    "На сервере не задан ключ шифрования VK_ADS_SECRET_KEY — без него токен негде хранить.",
};

/** Одна цель запуска кампании (зеркало `LaunchGoalOut` ядра, `services.goals.launch_goals()`). */
export type LaunchGoal = { code: string; title: string; implemented: boolean };

/** Итог запуска кампании (зеркало `CreativeLaunchOut` ядра) — общий для запуска
 * с креативом (`POST /briefs/{id}/creative`) и без него (`POST /briefs/{id}/launch`). */
export type LaunchOutcome = { campaign_status: string; campaign_id: number; message: string };

/** Карточка предпросмотра запуска (зеркало `LaunchPreviewOut` ядра) — мастер
 * запуска обязан показать её и ждать явного подтверждения, прежде чем тратить
 * деньги клиента (та же роль, что `render_launch_confirmation` в боте). */
export type LaunchPreview = {
  client_name: string | null;
  client_tax_id: string | null;
  object_url: string;
  surface_title: string;
  goal_title: string;
  budget_text: string;
  term_text: string;
  ad_account_id: number;
  ad_account_title: string;
  ad_account_external_id: string;
  ad_account_client_id: number | null;
  ad_account_client_name: string | null;
  ad_account_balance_rub: string | null;
  daily_budget_rub: number | null;
  balance_below_daily_budget: boolean;
  client_mismatch: boolean;
};

/** Причины отказа выбора кабинета при запуске (409-детали ядра) — тот же текст,
 * что бот показывает в `_cabinet_reject_reason` (bot/api_client.py). */
export const CABINET_REJECT_ERRORS: Record<string, string> = {
  ad_account_client_mismatch:
    "Этот кабинет закреплён за другим клиентом — деньги спишутся не с того счёта. " +
    "Выберите кабинет, закреплённый за клиентом брифа, либо общий.",
  advertiser_mismatch:
    "Конечный рекламодатель кабинета не совпадает с клиентом брифа (разошёлся ИНН). " +
    "Выберите другой кабинет либо проверьте бриф.",
  no_ad_account: "Ни одного рекламного кабинета не добавлено — запускать некуда.",
  ambiguous_ad_account: "Кабинетов несколько — выберите нужный явно.",
  ad_account_not_found: "Этот кабинет не найден — возможно, его уже удалили. Выберите другой.",
  ad_account_token_unavailable:
    "Рекламный кабинет недоступен: токен стёрт или кабинет удалён. Выберите другой кабинет.",
};

/** Причины отказа запуска/приёма креатива (422-детали ядра) — тот же текст,
 * что бот показывает в `_creative_reject_reason` (bot/api_client.py). */
export const LAUNCH_REJECT_ERRORS: Record<string, string> = {
  goal_not_supported:
    "Эта цель рекламы ещё не реализована. Доступны «Подписчики» и «Заявки — лид-форма».",
  senler_not_connected:
    "К сообществу не подключён чат-бот Senler — заявки будет некому обрабатывать. " +
    "Проверьте подключение и повторите запуск.",
  brief_not_found: "Бриф не найден.",
};

/** Известные отказы `POST /ad-accounts/agency-cabinets` — тот же текст, что
 * бот показывает в `_agency_cabinet_reject_reason` (bot/api_client.py), без
 * упоминания команды `/cabinets`: в вебе альтернатива — «выбрать кабинет вручную». */
const AGENCY_CABINET_ERRORS: Record<string, string> = {
  agency_disabled:
    "Автоматическое создание кабинетов пока выключено — агентский доступ VK ещё не " +
    "подтверждён. Выберите кабинет вручную или обратитесь к администратору.",
  tax_id_required:
    "У клиента не указан ИНН — без него кабинет не завести. Дособерите ИНН правкой " +
    "брифа и повторите.",
  client_not_found:
    "Такого клиента не нашли — возможно, бриф изменился. Обновите карточку брифа и " +
    "попробуйте снова.",
  encryption_key_missing:
    "Не получилось создать кабинет из-за технической настройки на сервере. Нужна " +
    "помощь администратора.",
  vk_oauth_not_configured: "Доступ агентства к VK ещё не настроен. Обратитесь к администратору.",
  vk_agency_not_confirmed:
    "VK не подтвердил агентский статус аккаунта — автоматически кабинет не завести. " +
    "Выберите кабинет вручную или обратитесь к администратору.",
  vk_rejected_client_data:
    "VK отклонил данные клиента при создании кабинета. Проверьте ФИО и ИНН в брифе и " +
    "попробуйте снова.",
  vk_client_not_found:
    "VK не нашёл только что созданного клиента. Попробуйте ещё раз через минуту.",
  vk_unreachable: "VK сейчас не отвечает. Попробуйте ещё раз через минуту.",
  vk_oauth_invalid_credentials:
    "VK не принял данные для собственного доступа агентства. Нужна помощь администратора.",
  vk_oauth_rejected:
    "VK отклонил запрос на собственный доступ агентства. Нужна помощь администратора.",
  vk_oauth_unavailable: "VK сейчас не отвечает. Попробуйте ещё раз через минуту.",
};

const AGENCY_CABINET_FALLBACK = "Кабинет создать не получилось. Обратитесь к администратору.";

/** Человеческая причина отказа заведения клиенту кабинета автоматически (шаг
 * «завести кабинет клиенту»). Половинчатые отказы ядра приходят структурой с
 * `vk_client_id` — операция уже что-то сделала в VK, молчать об этом нельзя. */
export function agencyCabinetErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (detail && typeof detail === "object") {
      const vkClientId = (detail as { vk_client_id?: unknown }).vk_client_id;
      const note = vkClientId ? ` Номер клиента в VK: ${String(vkClientId)}.` : "";
      const code = String((detail as { error?: unknown }).error ?? "");
      if (code === "token_issuance_failed") {
        return (
          "Клиента в VK завели, но подключить кабинет к системе не получилось." +
          note +
          " Обратитесь к администратору — донастроить нужно вручную."
        );
      }
      if (code === "cabinet_persist_failed") {
        return (
          "Кабинет в VK создан, но сохранить его в системе не получилось." +
          note +
          " Обратитесь к администратору — донастроить нужно вручную."
        );
      }
      if (code === "cabinet_duplicate") {
        return (
          "Клиента в VK завели, но кабинет с таким номером в системе уже есть — похоже " +
          "на дубль." +
          note +
          " Повторная попытка не поможет: обратитесь к администратору."
        );
      }
      if (code === "cabinet_client_gone") {
        return (
          "Клиента в VK завели, но клиент, для которого заводили кабинет, за это время " +
          "пропал — привязывать не к кому." +
          note +
          " Повторная попытка не поможет: обратитесь к администратору."
        );
      }
      return AGENCY_CABINET_FALLBACK;
    }
    if (typeof detail === "string") return AGENCY_CABINET_ERRORS[detail] ?? AGENCY_CABINET_FALLBACK;
  }
  return AGENCY_CABINET_FALLBACK;
}

/** Человеческая причина отказа запуска/приёма креатива по ошибке API — единая
 * точка для обоих действий шага подтверждения (с креативом и без). */
export function launchErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (detail && typeof detail === "object") {
      const issues = (detail as { issues?: unknown }).issues;
      if (Array.isArray(issues) && issues.length) return issues.join(" ");
      const missing = (detail as { missing?: unknown }).missing;
      if (Array.isArray(missing) && missing.length) {
        return `Бриф заполнен не полностью — не хватает полей: ${missing.join(", ")}. Вернитесь к первому шагу и внесите правки.`;
      }
    }
    if (typeof detail === "string") {
      if (error.status === 409)
        return CABINET_REJECT_ERRORS[detail] ?? "Рекламный кабинет недоступен.";
      return LAUNCH_REJECT_ERRORS[detail] ?? "Запустить не вышло.";
    }
  }
  return "Запустить не вышло.";
}

// --- кампании: остановка (зеркало `bot/handlers/stop_campaign.py`) ----------

/** Итог остановки кампании (зеркало `CampaignStopOut` ядра). `external_id`
 * пуст — на площадке останавливать было нечего (кампания не создавалась в
 * VK/kotbot), но статус у нас всё равно сменился на `stopped`. */
export type CampaignStopOut = { campaign_id: number; status: string; external_id: string | null };

/** Человеческий текст по итогу `POST /campaigns/{id}/stop` — те же формулировки,
 * что бот показывает в `bot/handlers/stop_campaign.py`: успех не имитируем, если
 * канал отказал (CLAUDE.md §7). */
export function campaignStopMessage(result: CampaignStopOut): string {
  if (result.external_id == null) {
    return (
      `Кампания №${result.campaign_id} помечена как остановленная.\n` +
      "На площадке останавливать было нечего: у кампании нет внешнего id (она не " +
      "создавалась в VK/kotbot)."
    );
  }
  return (
    `Кампания №${result.campaign_id} остановлена на площадке (id ${result.external_id}). ` +
    "Показы прекращены, деньги не тратятся."
  );
}

/** Ошибка остановки кампании: 404 — номер не найден, 502 — канал отказал (та же
 * граница, что `CampaignNotFound`/`CampaignStopFailed` в `bot/api_client.py`). */
export function campaignStopErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "Кампания не найдена — возможно, список устарел. Обновите страницу и повторите.";
  }
  return (
    "Канал не принял остановку — кампания могла остаться запущенной. " +
    "Проверьте канал (VK API / kotbot) и повторите."
  );
}

// --- статистика кабинетов (зеркало `bot/handlers/stats.py`) -----------------

export type CabinetItem = {
  id: string;
  name: string;
  status: string;
  launched_at: string;
  is_mock: boolean;
};

export type CabinetsOut = { items: CabinetItem[] };

export type StatsPeriod = "all" | "month" | "week";

export type StatsOut = {
  cabinet_id: string;
  period: string;
  shows: number;
  clicks: number;
  spent: number;
  results: number;
  ctr: number;
  cpc: number;
  cpl: number;
  is_mock: boolean;
};

/** Исход синка одного кабинета (зеркало `CabinetSyncOutcome` ядра,
 * `services.stats_sync.cabinet_sync_outcome`) — три честных состояния, не два:
 * `"updated"` — молча; `"nothing_to_update"` — нейтральная пометка (кампания вне
 * `launched`/`moderation`, синк площадку не спрашивал — это норма, не сбой);
 * `"failed"` — тревожная пометка (площадка не ответила). */
export type CabinetSyncOutcome = "updated" | "nothing_to_update" | "failed";

export type CabinetSyncOut = {
  ok: boolean;
  outcome: CabinetSyncOutcome;
  synced: number;
  failed: number;
  results: Record<string, string>;
};

/** Метрики кабинета как пары «подпись — значение» — тот же набор и порядок,
 * что `_render_stats` в `bot/handlers/stats.py`. Числа не округляем повторно:
 * `ctr`/`cpc`/`cpl` уже округлены в `services.cabinet_stats`. */
export function humanCabinetStats(stats: StatsOut): [string, string][] {
  return [
    ["Показы", String(Math.round(stats.shows))],
    ["Клики", String(Math.round(stats.clicks))],
    ["Расход", `${Math.round(stats.spent)} ₽`],
    ["Результаты", String(Math.round(stats.results))],
    ["CTR", `${stats.ctr}%`],
    ["CPC", `${stats.cpc} ₽`],
    ["CPL", `${stats.cpl} ₽`],
  ];
}

// --- каналы доставки (зеркало `GET /admin/channels`) ------------------------

export type UserbotSession = {
  sender_id: number;
  authorized: boolean;
  unreachable: boolean;
  phone_masked: string | null;
};

export type UserbotChannel = {
  configured: boolean;
  available: boolean;
  sessions: UserbotSession[];
};

export type KotbotChannel = { configured: boolean; healthy: boolean };

export type ChannelsOut = { userbot: UserbotChannel; kotbot: KotbotChannel };

// --- Senler: токен сообщества (зеркало `core/api/v1/senler.py`) -------------

export type CommunityTokenOut = {
  community_id: string;
  community_name: string;
  connected: boolean;
  reason: string;
};

/** Причины отказа привязки токена сообщества — тот же текст, что бот показывает
 * в `bot/api_client.py::add_community_token` (`_COMMUNITY_TOKEN_ERRORS` +
 * ветка 422 `community_unreachable`). */
export function communityTokenErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422 && error.detail === "community_unreachable") {
      return (
        "VK не подтвердил токен — проверьте, что он не истёк и выпущен именно для " +
        "сообщества клиента, и попробуйте ещё раз."
      );
    }
    if (error.status === 422) return "Проверьте токен и попробуйте ещё раз.";
    if (error.status === 500 && error.detail === "encryption_key_missing") {
      return (
        "На сервере не задан ключ шифрования VK_ADS_SECRET_KEY — без него токен " +
        "негде хранить. Нужна помощь администратора."
      );
    }
  }
  return "Внутренняя ошибка сервера, попробуйте ещё раз позже.";
}

/** Отвязка токена: 404 — активной привязки для сообщества не было (не сбой,
 * честная информация — тот же случай, что `CommunityTokenNotFound` в боте). */
export function communityTokenNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

// --- справочник площадок (зеркало команды `/surfaces` бота) -----------------

export type SurfaceOut = {
  kind: string;
  title: string;
  hint: string;
  available: boolean;
  goal: string;
  goal_title: string;
  needs_creative: boolean;
};

export type SurfacesOut = { items: SurfaceOut[] };
