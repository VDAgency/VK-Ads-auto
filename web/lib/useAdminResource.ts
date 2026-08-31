// Общий хук загрузки данных админки: даёт экрану различить «грузится» / «не
// получилось» / «готово», чего раньше не было — все списки глотали любую
// ошибку в `.catch(() => setItems([]))` и показывали пустой список вместо
// сообщения об ошибке (spec 2026-08-31 §7).

"use client";

import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "@/lib/adminApi";

export type Loadable<T> =
  { status: "loading" } | { status: "error" } | { status: "ready"; data: T };

/**
 * Тянет `path` через `adminFetch` при монтировании и при смене `path`/`deps`.
 * `retry()` перезапускает запрос, не трогая уже показанные данные, пока новый
 * не придёт (экран остаётся на состоянии «ошибка» до ответа).
 */
export function useAdminResource<T>(
  path: string,
  deps: React.DependencyList = [],
): [Loadable<T>, () => void] {
  const [state, setState] = useState<Loadable<T>>({ status: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    // Сброс на «грузится» при смене пути/`retry()` — намеренно синхронно:
    // экран обязан показать скелет, а не старые данные, пока идёт новый запрос.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ status: "loading" });
    adminFetch<T>(path)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, reloadKey, ...deps]);

  const retry = useCallback(() => setReloadKey((key) => key + 1), []);
  return [state, retry];
}
