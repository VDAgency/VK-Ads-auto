"use client";

// Код цели → человеческое название, из общего справочника `GET /admin/goals`
// (`services.goals.launch_goals()`) — того же, что показывает мастер запуска
// (`GoalStep.tsx`). Ни один код цели здесь не хардкодится (CLAUDE.md §1.3 —
// бизнес-логика и словари целей живут в ядре).
//
// Важная оговорка: `CampaignRow.objective` (список кампаний) хранит не код
// цели раскладки, а технический VK-объектив (`services.mapping.SOCIAL_
// ENGAGEMENT`, буквально `"socialengagement"`) — он ОДИН И ТОТ ЖЕ для всех
// четырёх целей брифа и никогда не совпадает с кодами `services.goals.
// launch_goals()` («subscribers»/«messages»/«lead_form»/«senler»). Поэтому
// сопоставление ниже сегодня всегда возвращает пусто для существующих
// кампаний — это не баг словаря, а честная граница: ядро пока не отдаёт
// на этот счёт отдельного поля. `CampaignList` не показывает необъяснённый
// код оператору (что и было дефектом), а просто опускает этот кусок подписи.
import { useAdminResource } from "@/lib/useAdminResource";
import type { LaunchGoal } from "@/lib/adminApi";

export function useGoalTitles(): Record<string, string> {
  const [state] = useAdminResource<{ items: LaunchGoal[] }>("/goals");
  if (state.status !== "ready") return {};
  return Object.fromEntries(state.data.items.map((goal) => [goal.code, goal.title]));
}
