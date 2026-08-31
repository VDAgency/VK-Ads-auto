"use client";

import { useState } from "react";

import { VARIANT_RU, type BriefAllItem, type BriefListItem, type Flash } from "@/lib/adminApi";
import { humanDate } from "@/lib/humanDate";
import { useAdminResource } from "@/lib/useAdminResource";

import { BriefCardView } from "../BriefCardView";
import { Badge, StatusBadge } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { Segmented } from "../ui/Segmented";
import { SkeletonRows } from "../ui/Skeleton";

type BriefStatus = "pending" | "recent" | "all";

/** «3 дня» / «11 дней» — падеж важен, оператор читает это десятки раз в день. */
function daysLabel(days: number): string {
  const mod10 = days % 10;
  const mod100 = days % 100;
  if (mod10 === 1 && mod100 !== 11) return `${days} день`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${days} дня`;
  return `${days} дней`;
}

function BriefList({
  status,
  onOpenBrief,
}: {
  status: BriefStatus;
  onOpenBrief: (id: number) => void;
}) {
  const [state, retry] = useAdminResource<{ items: BriefListItem[] }>(`/briefs?status=${status}`, [
    status,
  ]);

  if (state.status === "loading") return <SkeletonRows />;
  if (state.status === "error") return <ErrorState onRetry={retry} />;

  const items = state.data.items;
  if (!items.length) {
    return (
      <EmptyState
        title={
          status === "pending" ? "Никто не ждёт заполнения." : "За неделю брифов не приходило."
        }
      />
    );
  }

  return (
    <div className="adm-list">
      {items.map((item, index) => {
        const who = item.contact_name ? `${item.contact_name} — ${item.contact}` : item.contact;
        return (
          <Row
            key={`${item.contact}-${index}`}
            title={who}
            subtitle={`${VARIANT_RU[item.variant] ?? item.variant} · ${item.channel}`}
            extra={
              item.waiting_days > 0 ? (
                <Badge tone="warn">{daysLabel(item.waiting_days)}</Badge>
              ) : undefined
            }
            badge={item.brief_id ? <Badge tone="accent">открыть</Badge> : <Badge>ждём</Badge>}
            onClick={item.brief_id ? () => onOpenBrief(item.brief_id as number) : undefined}
          />
        );
      })}
    </div>
  );
}

function AllBriefList({ onOpenBrief }: { onOpenBrief: (id: number) => void }) {
  const [state, retry] = useAdminResource<{ items: BriefAllItem[] }>("/briefs?status=all");

  if (state.status === "loading") return <SkeletonRows />;
  if (state.status === "error") return <ErrorState onRetry={retry} />;

  const items = state.data.items;
  if (!items.length) {
    return (
      <EmptyState
        title="Брифов ещё не было."
        description="Как только клиент заполнит анкету — по приглашению, по своей ссылке или с сайта — она появится здесь."
      />
    );
  }

  return (
    <div className="adm-list">
      {items.map((item) => (
        <Row
          key={item.brief_id}
          title={item.client_name || "Без имени"}
          subtitle={`${VARIANT_RU[item.variant] ?? item.variant} · ${humanDate(item.created_at)}`}
          badge={<StatusBadge status={item.status} />}
          onClick={() => onOpenBrief(item.brief_id)}
        />
      ))}
    </div>
  );
}

export function BriefsScreen({
  briefId,
  onOpenBrief,
  onCloseBrief,
  flash,
  onFlash,
}: {
  briefId: number | null;
  onOpenBrief: (id: number) => void;
  onCloseBrief: () => void;
  flash: Flash;
  onFlash: (flash: Flash) => void;
}) {
  // По умолчанию — «Все»: раздел открывают, чтобы увидеть работу, а не пустой
  // экран. «Ждём»/«Пришли» строятся из приглашений и часто пусты (ничего не
  // просрочено или неделя тихая) — именно это и было дефектом: брифы по
  // реферальной ссылке и с холодного трафика не попадали ни туда, ни туда, и
  // оператор их попросту не видел. «Все» показывает всю ленту сразу.
  const [status, setStatus] = useState<BriefStatus>("all");

  return (
    <>
      {briefId != null ? (
        <BriefCardView id={briefId} onBack={onCloseBrief} onFlash={onFlash} />
      ) : (
        <>
          <Segmented
            value={status}
            onChange={setStatus}
            ariaLabel="Какие брифы показать"
            options={[
              { value: "all", label: "Все" },
              { value: "pending", label: "Ждём" },
              { value: "recent", label: "Пришли" },
            ]}
          />
          <div style={{ marginTop: "1rem" }}>
            {status === "all" ? (
              <AllBriefList onOpenBrief={onOpenBrief} />
            ) : (
              <BriefList key={status} status={status} onOpenBrief={onOpenBrief} />
            )}
          </div>
        </>
      )}
      {flash ? (
        <div className={`result show ${flash.ok ? "ok" : "err"}`} role="status">
          {flash.text}
        </div>
      ) : null}
    </>
  );
}
