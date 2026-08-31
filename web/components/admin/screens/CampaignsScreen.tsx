"use client";

import type { CampaignRow } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { StatusBadge } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { SkeletonRows } from "../ui/Skeleton";

export function CampaignsScreen() {
  const [state, retry] = useAdminResource<{ items: CampaignRow[] }>("/campaigns");

  if (state.status === "loading") return <SkeletonRows />;
  if (state.status === "error") return <ErrorState onRetry={retry} />;

  const items = state.data.items;
  if (!items.length) {
    return (
      <EmptyState
        title="Кампаний пока нет."
        description="Они появятся здесь после первого запуска рекламы по брифу."
      />
    );
  }

  return (
    <div className="adm-list">
      {items.map((campaign) => {
        // Кабинет, которым оплачена кампания (spec 2026-08-25 §3) — без него
        // расследовать ошибочный запуск можно было только запросом в базу.
        const cabinet = campaign.ad_account_title
          ? `кабинет «${campaign.ad_account_title}»${
              campaign.ad_account_external_id ? ` (id ${campaign.ad_account_external_id})` : ""
            }`
          : "кабинет не указан";
        return (
          <Row
            key={campaign.id}
            title={`Кампания №${campaign.id} · ${campaign.client_name || "—"}`}
            subtitle={`бриф №${campaign.brief_id} · ${campaign.objective} · ${cabinet}`}
            badge={<StatusBadge status={campaign.status} />}
          />
        );
      })}
    </div>
  );
}
