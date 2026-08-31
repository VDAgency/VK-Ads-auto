// Единый словарь бейджей на все зоны админки: одно и то же состояние —
// один и тот же цвет и подпись, откуда бы экран его ни показывал.
// Использует общий класс `.badge` из styles.css (тот же слой, что видит
// клиентский кабинет) — своей палитры здесь не заводим.

import type { ReactNode } from "react";

import { HEALTH_RU, STATUS_RU } from "@/lib/adminApi";

export type BadgeTone = "neutral" | "accent" | "warn" | "danger";

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={tone === "neutral" ? "badge" : `badge badge--${tone}`}>{children}</span>;
}

/** Тон статуса брифа/кампании — статусы у них общие (`status: str` в БД). */
const STATUS_TONE: Record<string, BadgeTone> = {
  received: "neutral",
  parsed: "neutral",
  prepared: "neutral",
  launched: "accent",
  moderation: "warn",
  stopped: "neutral",
  failed: "danger",
  // Легаси-значения статуса кабинета — см. `STATUS_RU` в `lib/adminApi.ts`.
  active: "accent",
  paused: "neutral",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{STATUS_RU[status] ?? status}</Badge>;
}

const HEALTH_TONE: Record<string, BadgeTone> = {
  healthy: "accent",
  ok: "accent", // синоним «жив», встречается в данных — см. `HEALTH_RU`.
  unauthorized: "danger",
  error: "warn",
  unknown: "neutral",
};

export function HealthBadge({ health }: { health: string }) {
  return <Badge tone={HEALTH_TONE[health] ?? "neutral"}>{HEALTH_RU[health] ?? health}</Badge>;
}
