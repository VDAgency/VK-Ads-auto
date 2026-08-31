"use client";

// Синк + чтение метрик ОДНОГО кабинета — паритет с `_answer_cabinet` бота
// (`bot/handlers/stats.py`): перед каждым показом (открытие кабинета, смена
// периода) сперва синкаем ИМЕННО этот кабинет (`POST /cabinets/{id}/stats/sync`),
// потом читаем сохранённые метрики (`GET /cabinets/{id}/stats`). Исход синка не
// блокирует показ — читаем метрики даже если синк вернул `outcome: "failed"":
// экран всё равно обязан показать последние сохранённые цифры (CLAUDE.md §7:
// не роняем показ из-за стороннего сбоя), только с честной пометкой.
//
// `useAdminResource` здесь не подходит: ему нужен один GET, а тут обязательная
// последовательность POST → GET перед каждым рендером.

import { useCallback, useEffect, useState } from "react";

import {
  adminFetch,
  type CabinetSyncOut,
  type CabinetSyncOutcome,
  type StatsOut,
} from "@/lib/adminApi";

export type CabinetStatsState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; data: StatsOut; outcome: CabinetSyncOutcome };

export function useCabinetStats(
  cabinetId: string,
  period: "all" | "month" | "week",
): [CabinetStatsState, () => void] {
  const [state, setState] = useState<CabinetStatsState>({ status: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ status: "loading" });

    async function run() {
      // Сбой синка — честная пометка, не повод не показать сохранённые данные.
      let outcome: CabinetSyncOutcome = "failed";
      try {
        const sync = await adminFetch<CabinetSyncOut>(`/cabinets/${cabinetId}/stats/sync`, {
          method: "POST",
        });
        outcome = sync.outcome;
      } catch {
        outcome = "failed";
      }
      try {
        const data = await adminFetch<StatsOut>(`/cabinets/${cabinetId}/stats?period=${period}`);
        if (!cancelled) setState({ status: "ready", data, outcome });
      } catch {
        if (!cancelled) setState({ status: "error" });
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [cabinetId, period, reloadKey]);

  const retry = useCallback(() => setReloadKey((key) => key + 1), []);
  return [state, retry];
}
