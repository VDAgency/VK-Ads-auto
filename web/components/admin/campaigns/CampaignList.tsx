"use client";

// Список кампаний: паритет со `/stop_campaign` бота (spec 2026-08-31-admin-
// cabinet-design-brief.md, задача 1). Статус — общий `StatusBadge` (переиспользуем,
// свой словарь не заводим). Остановка — необратимое действие: подтверждение
// перед вызовом (`window.confirm`, тот же приём, что удаление кабинета в
// `AdAccounts.tsx`, — родной диалог браузера полностью управляется с клавиатуры
// и закрывается по Esc сам по себе), успех не имитируется при отказе канала
// (`bot/handlers/stop_campaign.py`, CLAUDE.md §7).

import { useState } from "react";

import {
  adminFetch,
  campaignStopErrorMessage,
  campaignStopMessage,
  type CampaignRow,
  type CampaignStopOut,
  type Flash,
} from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { StatusBadge } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { SkeletonRows } from "../ui/Skeleton";
import { useGoalTitles } from "./useGoalTitles";

// Кампанию можно остановить, только пока она реально откручивается или ждёт
// модерации — тот же список статусов, что читает `services.launch_service.
// stop_campaign`/`db.repositories.list_active_campaigns` (CLAUDE.md §1.3: сама
// проверка живёт в ядре, здесь только не рисуем кнопку там, где ядро её и так
// откажет 502/404 — не изобретаем новое правило, а просто не тратим клик).
const STOPPABLE = new Set(["launched", "moderation"]);

export function CampaignList() {
  const [state, retry] = useAdminResource<{ items: CampaignRow[] }>("/campaigns");
  const goalTitles = useGoalTitles();
  const [stoppingId, setStoppingId] = useState<number | null>(null);
  const [flash, setFlash] = useState<Flash>(null);

  if (state.status === "loading") return <SkeletonRows />;
  if (state.status === "error") return <ErrorState onRetry={retry} />;

  const items = state.data.items;

  async function stop(campaign: CampaignRow) {
    const confirmed = window.confirm(
      `Остановить кампанию №${campaign.id}?\n\n` +
        "Показы прекратятся, деньги перестанут тратиться. Возобновить кампанию потом нельзя.",
    );
    if (!confirmed) return;
    setStoppingId(campaign.id);
    try {
      const result = await adminFetch<CampaignStopOut>(`/campaigns/${campaign.id}/stop`, {
        method: "POST",
      });
      setFlash({ text: campaignStopMessage(result), ok: true, persistent: true });
      retry();
    } catch (error) {
      setFlash({ text: campaignStopErrorMessage(error), ok: false, persistent: true });
    } finally {
      setStoppingId(null);
    }
  }

  return (
    <>
      {!items.length ? (
        <EmptyState
          title="Кампаний пока нет."
          description="Они появятся здесь после первого запуска рекламы по брифу."
        />
      ) : (
        <div className="adm-list">
          {items.map((campaign) => {
            const cabinet = campaign.ad_account_title
              ? `кабинет «${campaign.ad_account_title}»${
                  campaign.ad_account_external_id ? ` (id ${campaign.ad_account_external_id})` : ""
                }`
              : "кабинет не указан";
            // Код цели ядро сегодня не отдаёт по кампании отдельно от техничес-
            // кого VK-объектива (см. `useGoalTitles.ts`) — не найден код, кусок
            // подписи просто опускается, а не показывается сырым (дефект,
            // который чинит эта задача: было видно "socialengagement").
            const goal = goalTitles[campaign.objective];
            const subtitle = [`бриф №${campaign.brief_id}`, goal, cabinet]
              .filter(Boolean)
              .join(" · ");
            const stoppable = STOPPABLE.has(campaign.status);
            return (
              <Row
                key={campaign.id}
                title={`Кампания №${campaign.id} · ${campaign.client_name || "—"}`}
                subtitle={subtitle}
                badge={<StatusBadge status={campaign.status} />}
                extra={
                  stoppable ? (
                    <button
                      className="btn btn--danger"
                      type="button"
                      disabled={stoppingId === campaign.id}
                      onClick={() => void stop(campaign)}
                    >
                      {stoppingId === campaign.id ? "Останавливаем…" : "Остановить"}
                    </button>
                  ) : undefined
                }
              />
            );
          })}
        </div>
      )}
      {flash ? (
        <div className={`result show ${flash.ok ? "ok" : "err"}`} role="status">
          {flash.text}
        </div>
      ) : null}
    </>
  );
}
