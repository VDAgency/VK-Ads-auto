"use client";

// Статистика по кабинетам: паритет с `/stats` бота (`bot/handlers/stats.py`).
// Список кабинетов → выбор одного → метрики за период с сегментным
// переключателем «Всё время / Месяц / Неделя». Синк перед показом и три честных
// исхода — в `useCabinetStats.ts`.

import { useState } from "react";

import { STATUS_RU, humanCabinetStats, type CabinetItem, type StatsPeriod } from "@/lib/adminApi";
import { humanDate } from "@/lib/humanDate";
import { useAdminResource } from "@/lib/useAdminResource";

import { StatusBadge } from "../ui/Badge";
import { BackLink } from "../ui/BackLink";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { Segmented } from "../ui/Segmented";
import { SkeletonRows } from "../ui/Skeleton";
import { useCabinetStats } from "./useCabinetStats";

const PERIOD_OPTIONS: { value: StatsPeriod; label: string }[] = [
  { value: "all", label: "Всё время" },
  { value: "month", label: "Месяц" },
  { value: "week", label: "Неделя" },
];

function CabinetStatsDetail({ cabinet, onBack }: { cabinet: CabinetItem; onBack: () => void }) {
  const [period, setPeriod] = useState<StatsPeriod>("all");
  const [state, retry] = useCabinetStats(cabinet.id, period);

  return (
    <div className="adm-card">
      <BackLink label="Назад к кабинетам" onClick={onBack} />
      <div className="adm-card__head">
        <h2>{cabinet.name}</h2>
        <StatusBadge status={cabinet.status} />
      </div>

      <Segmented
        value={period}
        onChange={setPeriod}
        ariaLabel="Период статистики"
        options={PERIOD_OPTIONS}
      />

      <div style={{ marginTop: "1rem" }}>
        {state.status === "loading" ? <SkeletonRows count={4} /> : null}
        {state.status === "error" ? (
          <ErrorState message="Не удалось загрузить статистику кабинета." onRetry={retry} />
        ) : null}
        {state.status === "ready" ? (
          <>
            {state.data.is_mock ? (
              <p className="note">⚠️ Демо-данные. Реальные появятся после подключения VK.</p>
            ) : null}
            {state.outcome === "failed" ? (
              <div className="adm-drop__error" role="alert">
                Не удалось обновить данные, показаны последние сохранённые.
              </div>
            ) : null}
            {state.outcome === "nothing_to_update" ? (
              <p className="note">Новых данных пока нет — показаны последние сохранённые цифры.</p>
            ) : null}
            <dl className="adm-fields" style={{ marginTop: "1rem" }}>
              {humanCabinetStats(state.data).map(([label, value]) => (
                <div className="adm-field" key={label}>
                  <dt className="adm-field__n">—</dt>
                  <dd className="adm-field__label">{label}</dd>
                  <dd className="adm-field__value">{value}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : null}
      </div>
    </div>
  );
}

export function CabinetStats() {
  const [state, retry] = useAdminResource<{ items: CabinetItem[] }>("/cabinets");
  const [selected, setSelected] = useState<CabinetItem | null>(null);

  if (state.status === "loading") return <SkeletonRows count={3} />;
  if (state.status === "error") return <ErrorState onRetry={retry} />;

  const items = state.data.items;

  if (selected) {
    return <CabinetStatsDetail cabinet={selected} onBack={() => setSelected(null)} />;
  }

  if (!items.length) {
    return (
      <EmptyState
        title="Кабинетов пока нет."
        description="Статистика появится, как только реклама будет запущена хотя бы в одном кабинете."
      />
    );
  }

  return (
    <>
      {items.some((cabinet) => cabinet.is_mock) ? (
        <p className="note">⚠️ Демо-данные. Реальные появятся после подключения VK.</p>
      ) : null}
      <div className="adm-list">
        {items.map((cabinet) => (
          <Row
            key={cabinet.id}
            title={cabinet.name}
            subtitle={`${STATUS_RU[cabinet.status] ?? cabinet.status} · запущен ${humanDate(cabinet.launched_at)}`}
            onClick={() => setSelected(cabinet)}
          />
        ))}
      </div>
    </>
  );
}
