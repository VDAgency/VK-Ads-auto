// Тонкая обёртка над apiFetch для операторских эндпоинтов `/api/v1/admin/*`.
// Своей логики не несёт — только префикс, чтобы он не размазывался по экранам.

import { apiFetch } from "@/lib/api";

export function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  return apiFetch<T>(`/admin${path}`, init);
}

export const STATUS_RU: Record<string, string> = {
  received: "Принят",
  parsed: "В работе",
  prepared: "Готов к запуску",
  launched: "Запущена",
  failed: "Ошибка",
};

export const VARIANT_RU: Record<string, string> = {
  individual: "Физлицо",
  community: "Бизнес",
};

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

export type CampaignRow = {
  id: number;
  brief_id: number;
  client_name: string | null;
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
  };
  fields: BriefField[];
  has_creative: boolean;
  campaign_status?: string | null;
  /** Распознанная площадка подписки — считает ядро, здесь только показываем. */
  surface_title?: string;
  /** Нужен ли креатив: продвижение готового поста обходится без него. */
  surface_needs_creative?: boolean;
  unknown?: number[];
};

export type InviteResult = {
  invite_id: number;
  status: string;
  channel: string;
  fallback_text: string | null;
  error: string | null;
};

/** Сообщение в общей полосе результата админки. */
export type Flash = { text: string; ok: boolean } | null;

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
  status: string;
  health: string;
  health_checked_at: string | null;
  health_error: string | null;
  balance_rub: string | null;
  is_usable: boolean;
};

/** Человеческие подписи состояний health-check (те же, что в боте). */
export const HEALTH_RU: Record<string, string> = {
  healthy: "✅ жив",
  unauthorized: "⛔ токен не принят",
  error: "⚠️ VK не ответил",
  unknown: "… не проверялся",
};

/** Причины отказа добавления кабинета — по коду `detail` из ядра. */
export const AD_ACCOUNT_ERRORS: Record<string, string> = {
  invalid_token: "VK не принял этот токен. Проверьте, что скопирован весь access_token.",
  duplicate_account: "Такой кабинет уже добавлен.",
  vk_unreachable: "VK сейчас не отвечает. Попробуйте ещё раз через минуту.",
  encryption_key_missing:
    "На сервере не задан ключ шифрования VK_ADS_SECRET_KEY — без него токен негде хранить.",
};
