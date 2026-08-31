"use client";

import { useEffect, useState } from "react";

import {
  adminFetch,
  isHealthBad,
  type AdAccount,
  type BriefListItem,
  type CampaignRow,
  type Overview,
} from "@/lib/adminApi";

import { Badge } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { SkeletonRows, SkeletonTiles } from "../ui/Skeleton";

type AttentionItem = { id: string; text: string; tone: "warn" | "danger"; hash: string };

type Data = {
  overview: Overview;
  attention: AttentionItem[];
};

type State = { status: "loading" } | { status: "error" } | { status: "ready"; data: Data };

/**
 * Собирает ленту «требует внимания» из того, что уже отдаёт ядро: кампании с
 * ошибкой запуска, кабинеты с непринятым токеном, брифы, которые долго ждём.
 * Своей бизнес-логики здесь нет — только фильтрация и подписи для готовых
 * списков (то же самое, что уже делают `BriefList`/`CampaignList`).
 *
 * Не полный список категорий из spec 2026-08-31 §4: «брифы без запуска»
 * (присланный, но не превратившийся в кампанию) требует статуса брифа,
 * которого сегодняшний `/briefs` не отдаёт построчно — оставлено на
 * следующую задачу, чтобы не тянуть N+1 запросов за каждой карточкой.
 */
function buildAttention(
  campaigns: CampaignRow[] | null,
  adAccounts: AdAccount[] | null,
  pendingBriefs: BriefListItem[] | null,
): AttentionItem[] {
  const items: AttentionItem[] = [];

  for (const campaign of campaigns ?? []) {
    if (campaign.status === "failed") {
      items.push({
        id: `campaign-${campaign.id}`,
        text: `Кампания №${campaign.id} (${campaign.client_name || "без клиента"}) — ошибка запуска`,
        tone: "danger",
        hash: "#/campaigns",
      });
    }
  }

  for (const account of adAccounts ?? []) {
    if (isHealthBad(account.health)) {
      items.push({
        id: `ad-account-${account.id}`,
        text: `Кабинет «${account.title}» — ${
          account.health === "unauthorized" ? "токен не принят" : "VK не ответил на проверку"
        }`,
        tone: account.health === "unauthorized" ? "danger" : "warn",
        hash: "#/channels",
      });
    }
  }

  for (const brief of pendingBriefs ?? []) {
    if (brief.waiting_days >= 3) {
      items.push({
        id: `pending-${brief.contact}`,
        text: `${brief.contact_name || brief.contact} — ждём бриф уже ${brief.waiting_days} дн.`,
        tone: "warn",
        hash: "#/briefs",
      });
    }
  }

  return items;
}

export function OverviewScreen({ onNavigate }: { onNavigate: (hash: string) => void }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setState({ status: "loading" });
      // Счётчики обязательны — без них экрану нечего показать. Остальное
      // (кампании/кабинеты/ожидающие брифы для ленты внимания) — необязательно:
      // то же правило, что раньше было у плиток на этом экране («плитки — не
      // критично, без них панель остаётся работоспособной»).
      let overview: Overview;
      try {
        overview = await adminFetch<Overview>("/overview");
      } catch {
        if (!cancelled) setState({ status: "error" });
        return;
      }

      const [campaigns, adAccounts, pending] = await Promise.all([
        adminFetch<{ items: CampaignRow[] }>("/campaigns").catch(() => null),
        adminFetch<{ items: AdAccount[] }>("/ad-accounts").catch(() => null),
        adminFetch<{ items: BriefListItem[] }>("/briefs?status=pending").catch(() => null),
      ]);

      if (cancelled) return;
      setState({
        status: "ready",
        data: {
          overview,
          attention: buildAttention(
            campaigns?.items ?? null,
            adAccounts?.items ?? null,
            pending?.items ?? null,
          ),
        },
      });
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  if (state.status === "loading") {
    return (
      <>
        <SkeletonTiles count={4} />
        <h2 className="section-title">Требует внимания</h2>
        <SkeletonRows count={3} />
      </>
    );
  }

  if (state.status === "error") {
    return (
      <ErrorState
        message="Не удалось загрузить обзор."
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }

  const { overview, attention } = state.data;

  return (
    <>
      <div className="adm-tiles">
        <button className="adm-tile" type="button" onClick={() => onNavigate("#/clients")}>
          <b>{overview.clients}</b>
          <span>Клиенты</span>
        </button>
        <button
          className={overview.pending > 0 ? "adm-tile is-alert" : "adm-tile"}
          type="button"
          onClick={() => onNavigate("#/briefs")}
        >
          <b>{overview.pending}</b>
          <span>Ждём брифы</span>
        </button>
        <button className="adm-tile" type="button" onClick={() => onNavigate("#/briefs")}>
          <b>{overview.recent}</b>
          <span>Пришли за неделю</span>
        </button>
        <button className="adm-tile" type="button" onClick={() => onNavigate("#/campaigns")}>
          <b>{overview.campaigns}</b>
          <span>Кампании</span>
        </button>
      </div>

      <h2 className="section-title">Требует внимания</h2>
      {attention.length ? (
        <div className="adm-list">
          {attention.map((item) => (
            <Row
              key={item.id}
              title={item.text}
              badge={
                <Badge tone={item.tone}>{item.tone === "danger" ? "чинить" : "проверить"}</Badge>
              }
              onClick={() => onNavigate(item.hash)}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          title="Сейчас ничего не горит."
          description="Кампании запускаются без ошибок, кабинеты на связи, брифов в долгом ожидании нет."
        />
      )}
    </>
  );
}
