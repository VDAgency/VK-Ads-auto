"use client";

// Шаг 2 мастера — цель рекламы (spec 2026-08-31-admin-cabinet-design-brief.md §5).
// Список — общий с ботом (`GET /admin/goals` → `services.goals.launch_goals()`,
// CLAUDE.md §1.3: список не должен жить в обработчике канала). Нереализованную
// цель показываем, но не даём выбрать — молча прятать нельзя.

import { type LaunchGoal } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { ErrorState } from "../ui/ErrorState";
import { SkeletonRows } from "../ui/Skeleton";

export function GoalStep({ onPicked }: { onPicked: (goal: LaunchGoal) => void }) {
  const [state, retry] = useAdminResource<{ items: LaunchGoal[] }>("/goals");

  if (state.status === "loading") return <SkeletonRows count={4} />;
  if (state.status === "error") {
    return <ErrorState message="Не удалось загрузить список целей." onRetry={retry} />;
  }

  return (
    <div className="adm-list">
      {state.data.items.map((goal) =>
        goal.implemented ? (
          <button className="adm-row" type="button" key={goal.code} onClick={() => onPicked(goal)}>
            <span className="adm-row__main">
              <span className="adm-row__title">{goal.title}</span>
            </span>
          </button>
        ) : (
          <div className="adm-row adm-row--static" key={goal.code} aria-disabled="true">
            <span className="adm-row__main">
              <span className="adm-row__title">{goal.title}</span>
              <span className="adm-row__meta">Эта цель ещё не реализована — скоро.</span>
            </span>
          </div>
        ),
      )}
    </div>
  );
}
