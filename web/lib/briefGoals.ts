/**
 * Вкладки «цель рекламы» в брифе и площадки, доступные внутри каждой.
 *
 * Раньше цель и площадка были двумя независимыми вопросами: «Куда привлекаем
 * подписчиков?» (все 15 площадок одним списком) и, только в брифе бизнеса,
 * необязательный вопрос «Что хотите получить?», значение которого сервер вообще
 * не читал (`services/brief_parser.py` выводит цель из `target_type`, а не из
 * поля `goal`). Половина площадок в первом списке не имела отношения к
 * заголовку («лид-форма» не привлекает подписчиков, «сообщения» ведут в диалог).
 *
 * Теперь клиент выбирает цель вкладкой, а площадка внутри неё — из
 * отфильтрованного набора. Источник группировки — поле `goal` каждой площадки
 * в `web/lib/briefSurfaces.ts`, которое само зеркалит
 * `integrations/vk_surfaces.py` (Python-справочник — точка правды; сверка —
 * `tests/test_brief_goal_tabs.py`).
 *
 * Порядок и состав вкладок — решение заказчика 2026-08-23
 * (`docs/superpowers/specs/2026-08-23-brief-goal-tabs-design.md`): пять вкладок,
 * а не четыре, потому что «вовлечение в готовый объект» (пост/трек/клип) — это
 * не то же самое, что «подписчики», хотя обе цели ведут через один и тот же
 * список площадок подписки в справочнике. Заявка через Senler показывается,
 * но недоступна: клиент видит замысел сервиса целиком, но выбрать её не может
 * («enabled: false» — вкладка рендерится с атрибутом `disabled`).
 */
import { type BriefGoalKey, goalOfSurfaceValue, surfacesForGoal } from "./briefSurfaces";

export type BriefGoalTab = {
  /** Ключ цели; для «скоро»-вкладки без площадок в справочнике — `"senler"`. */
  key: BriefGoalKey | "senler";
  label: string;
  /** false → вкладка заблокирована, помечена «скоро», кликнуть нельзя. */
  enabled: boolean;
  /** Подсказки поля «ссылка на объект» — свои для каждой цели (требование §4 спеки). */
  objectUrl: {
    label: string;
    hint: string;
    placeholder: string;
  };
};

export const BRIEF_GOAL_TABS: BriefGoalTab[] = [
  {
    key: "subscription",
    label: "Подписчики",
    enabled: true,
    objectUrl: {
      label: "Ссылка на сообщество или страницу, куда привлекаем подписчиков",
      hint: "Ссылка на выбранный объект: сообщество, личную страницу, канал или рассылку — куда ведём рекламу",
      placeholder: "https://vk.com/your_group",
    },
  },
  {
    key: "engagement",
    label: "Вовлечение в готовый объект",
    enabled: true,
    objectUrl: {
      label: "Ссылка на пост, трек или клип, который продвигаем",
      hint: "Ссылка именно на выбранный объект (конкретный пост, трек или клип), а не на профиль или сообщество целиком",
      placeholder: "https://vk.com/wall-123_456",
    },
  },
  {
    key: "messages",
    label: "Сообщения сообществу",
    enabled: true,
    objectUrl: {
      label: "Ссылка на сообщество с включёнными сообщениями",
      hint: "В сообществе должны быть включены сообщения от лица группы — иначе реклама не сработает",
      placeholder: "https://vk.com/your_group",
    },
  },
  {
    key: "leads",
    label: "Заявки — лид-форма",
    enabled: true,
    objectUrl: {
      label: "Ссылка на лид-форму из кабинета VK",
      hint: "Создайте форму в интерфейсе VK Рекламы и скопируйте её адрес — он выглядит как leadads://<номер>/",
      placeholder: "leadads://857898/",
    },
  },
  {
    // Цели нет в справочнике вовсе (integrations/vk_surfaces.py её не заводит) —
    // не «непроверенная площадка», а нереализованная цель целиком.
    key: "senler",
    label: "Заявка через Senler",
    enabled: false,
    objectUrl: { label: "", hint: "", placeholder: "" },
  },
];

/** Площадки вкладки; у «скоро»-вкладки (Senler) их в справочнике нет. */
export function surfacesForTab(tab: BriefGoalTab): ReturnType<typeof surfacesForGoal> {
  if (tab.key === "senler") return [];
  return surfacesForGoal(tab.key);
}

/** Вкладка по умолчанию — первая доступная. */
export const DEFAULT_GOAL_TAB: BriefGoalTab =
  BRIEF_GOAL_TABS.find((tab) => tab.enabled) ?? BRIEF_GOAL_TABS[0];

/** Вкладка, которой принадлежит площадка с данным значением payload.
 *
 * Переиспользует `goalOfSurfaceValue` (web/lib/briefSurfaces.ts) вместо
 * повторного поиска по `BRIEF_SURFACES` — раньше здесь была продублирована
 * та же логика поиска площадки по значению. */
export function tabForSurfaceValue(value: string): BriefGoalTab | undefined {
  const goal = goalOfSurfaceValue(value);
  if (!goal) return undefined;
  return BRIEF_GOAL_TABS.find((tab) => tab.key === goal);
}
