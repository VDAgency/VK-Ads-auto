/**
 * Площадки подписки, предлагаемые клиенту в брифе.
 *
 * Цель «подписчики» ведёт не в одно место: подписаться можно на сообщество,
 * личную страницу, рассылку, канал ВКонтакте, канал MAX и на два объекта в
 * Одноклассниках. Все они идут через один рекламный кабинет — различаются
 * пакетом VK и набором допустимых форматов креатива.
 *
 * Значения (`value`) разбирает `parse_target_type` в services/brief_parser.py,
 * а ключи площадок живут в integrations/vk_surfaces.py. Менять формулировки
 * здесь можно только вместе с разбором — иначе выбор клиента молча
 * превратится в «сообщество».
 *
 * КАК ВКЛЮЧИТЬ ПЛОЩАДКУ: `enabled: false` → `true` после боевой проверки.
 * Заблокированные варианты рендерятся с атрибутом `disabled`, поэтому не
 * попадают в `FormData` и физически не могут уехать в ядро.
 *
 * Поле `goal` дублирует `integrations.vk_surfaces.Surface.goal` — по нему
 * `web/lib/briefGoals.ts` группирует площадки по вкладкам. Расхождение с
 * бэкендом ловит `tests/test_brief_goal_tabs.py::test_brief_surfaces_ts_matches_catalog`
 * (сверяет этот список с `services.goals.subscription_targets()`).
 */
export type BriefGoalKey = "subscription" | "engagement" | "messages" | "leads" | "senler";

export type BriefSurfaceOption = {
  /** Значение, уходящее в payload брифа (ключ поля — `target_type`). */
  value: string;
  label: string;
  /** false → карточка заблокирована и помечена «скоро». */
  enabled: boolean;
  /** Цель кампании площадки — определяет, на какой вкладке она показана. */
  goal: BriefGoalKey;
  /** Короткая подсказка под подписью варианта — сейчас только у Дзена (задача 7,
   * spec §C): минимальный бюджет площадки заметно выше остальных, и клиент должен
   * узнать об этом до отправки брифа, а не только после отказа запуска. */
  note?: string;
};

export const BRIEF_SURFACES: BriefSurfaceOption[] = [
  { value: "сообщество", label: "👥 Сообщество ВКонтакте", enabled: true, goal: "subscription" },
  {
    value: "личная страница",
    label: "👤 Личная страница ВКонтакте",
    enabled: true,
    goal: "subscription",
  },
  { value: "рассылка", label: "✉️ Рассылка ВКонтакте", enabled: true, goal: "subscription" },
  { value: "канал ВКонтакте", label: "📺 Канал ВКонтакте", enabled: true, goal: "subscription" },
  { value: "канал MAX", label: "🅼 Канал MAX", enabled: true, goal: "subscription" },
  {
    value: "сообщество в Одноклассниках",
    label: "🟠 Сообщество в Одноклассниках",
    enabled: true,
    goal: "subscription",
  },
  {
    value: "профиль в Одноклассниках",
    label: "🟠 Профиль в Одноклассниках",
    enabled: true,
    goal: "subscription",
  },
  {
    value: "канал Дзен",
    label: "📄 Канал Дзен",
    enabled: true,
    goal: "subscription",
    note: "Минимальный бюджет у Дзена — 10 000 ₽ в день.",
  },
  // Смежные цели: продвигаем готовый объект или собираем заявки. Креатив для постов
  // не нужен — объявлением служит сам пост.
  {
    value: "пост сообщества",
    label: "📝 Пост сообщества ВКонтакте",
    enabled: true,
    goal: "engagement",
  },
  // Название повторяет integrations.vk_surfaces.VK_POST_PERSONAL.title целиком —
  // без «ВКонтакте» расходилось с бэкендом (поймано тестом на сверку каталогов).
  {
    value: "пост личной страницы",
    label: "📝 Пост личной страницы ВКонтакте",
    enabled: true,
    goal: "engagement",
  },
  {
    value: "пост со ссылкой на сайт",
    label: "🔗 Пост с переходом на сайт",
    enabled: true,
    goal: "engagement",
  },
  { value: "музыка", label: "🎵 Музыка ВКонтакте", enabled: true, goal: "engagement" },
  { value: "клип", label: "🎬 Клип ВКонтакте", enabled: false, goal: "engagement" },
  { value: "лид-форма", label: "📋 Лид-форма ВКонтакте", enabled: true, goal: "leads" },
  // Требование договора (ТЗ §3): объект «Сообщество», цель «Написать сообщение».
  // Пакет VK (3127) прошёл боевой зонд 2026-08-23 (package_id/objective/10 из 13
  // шаблонов подтверждены), integrations/vk_surfaces.VK_MESSAGES.verified=True —
  // включена. Значение кнопки пока догадка (docs/VK_API_REFERENCE.md), но это не
  // мешает выбору площадки — на неё не влияет.
  {
    value: "написать сообщение",
    label: "✉️ Сообщения сообществу ВКонтакте",
    enabled: true,
    goal: "messages",
  },
  // Решение 2026-08-24: технически тот же пакет VK 3127, что у «Сообщений» выше —
  // боевая кампания 28694299, прочитанная напрямую из VK, подтвердила
  // {"objective": "socialengagement", "package_id": 3127}. Собственный боевой
  // прогон под именем Senler руководитель провёл в тот же день на сообществе
  // DJ BEAUTY (228817082): токен привязан, бриф принят, кампания создана и
  // прочитана напрямую из VK с верным пакетом/целью/префиксом имени —
  // integrations/vk_surfaces.VK_SENLER.verified=True, площадка открыта.
  {
    value: "заявка через senler",
    label: "🤖 Заявка через Senler",
    enabled: true,
    goal: "senler",
  },
];

/** Площадки одной цели — источник для вкладок `web/lib/briefGoals.ts`. */
export function surfacesForGoal(goal: BriefGoalKey): BriefSurfaceOption[] {
  return BRIEF_SURFACES.filter((surface) => surface.goal === goal);
}

/** Цель площадки по её значению payload; неизвестное значение — `undefined`. */
export function goalOfSurfaceValue(value: string): BriefGoalKey | undefined {
  return BRIEF_SURFACES.find((surface) => surface.value === value)?.goal;
}
