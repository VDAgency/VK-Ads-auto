"use client";

// «Площадки размещения»: справочник, веб-зеркало команды `/surfaces` бота
// (`bot/handlers/surfaces.py`) — какие площадки доступны по целям, что писать
// в поле брифа «Куда привлекаем» и нужен ли креатив. Это справка, а не
// настройка: площадку меняют правкой поля брифа, не здесь.

import type { SurfaceOut } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { Badge } from "../ui/Badge";
import { ErrorState } from "../ui/ErrorState";
import { SkeletonRows } from "../ui/Skeleton";

function groupByGoal(items: SurfaceOut[]): Map<string, SurfaceOut[]> {
  const groups = new Map<string, SurfaceOut[]>();
  for (const item of items) {
    const list = groups.get(item.goal_title) ?? [];
    list.push(item);
    groups.set(item.goal_title, list);
  }
  return groups;
}

export function SurfacesReference() {
  const [state, retry] = useAdminResource<{ items: SurfaceOut[] }>("/surfaces");

  if (state.status === "loading") return <SkeletonRows count={3} />;
  if (state.status === "error") {
    return <ErrorState message="Не удалось загрузить справочник площадок." onRetry={retry} />;
  }

  const groups = groupByGoal(state.data.items);

  return (
    <div className="adm-card">
      <p className="note">
        Площадка берётся из поля «Куда привлекаем» в брифе. Чтобы сменить её, откройте карточку
        брифа и внесите правку в это поле.
      </p>
      {Array.from(groups.entries()).map(([goalTitle, surfaces]) => (
        <div key={goalTitle} style={{ marginTop: "1.25rem" }}>
          <h3 style={{ fontSize: "var(--fs-sm)", margin: "0 0 0.5rem" }}>{goalTitle}</h3>
          <div className="adm-list">
            {surfaces.map((surface) => (
              <div className="adm-row adm-row--static" key={surface.kind}>
                <span className="adm-row__main">
                  <span className="adm-row__title">
                    {surface.title}
                    {!surface.needs_creative ? " (креатив не нужен)" : ""}
                  </span>
                  <span className="adm-row__meta">
                    в бриф: «{surface.kind}» или своими словами · {surface.hint}
                  </span>
                </span>
                <Badge tone={surface.available ? "accent" : "neutral"}>
                  {surface.available ? "доступна" : "скоро"}
                </Badge>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
