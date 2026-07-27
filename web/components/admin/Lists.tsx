"use client";

import { useEffect, useState } from "react";

import {
  adminFetch,
  contactLine,
  STATUS_RU,
  VARIANT_RU,
  type BriefListItem,
  type CampaignRow,
  type ClientDetail,
  type ClientRow,
} from "@/lib/adminApi";

/** Строка списка. Прежде — div с обработчиком клика; теперь кнопка (клавиатура). */
function Row({
  title,
  subtitle,
  badge,
  badgeKind,
  extra,
  onClick,
}: {
  title: string;
  subtitle?: string;
  badge: string;
  badgeKind?: "accent" | "wait";
  /** Дополнительный бейдж слева от основного (например, срок ожидания). */
  extra?: { text: string; kind: "wait" };
  onClick?: () => void;
}) {
  return (
    <button className="adm-row" type="button" onClick={onClick}>
      <span className="adm-row__main">
        <span className="adm-row__title">{title}</span>
        {subtitle ? <span className="adm-row__meta">{subtitle}</span> : null}
      </span>
      <span className="adm-row__side">
        {extra ? <span className={`adm-badge adm-badge--${extra.kind}`}>{extra.text}</span> : null}
        <span className={badgeKind ? `adm-badge adm-badge--${badgeKind}` : "adm-badge"}>
          {badge}
        </span>
      </span>
    </button>
  );
}

/** «3 дня» / «11 дней» — падеж важен, оператор читает это десятки раз в день. */
function daysLabel(days: number): string {
  const mod10 = days % 10;
  const mod100 = days % 100;
  if (mod10 === 1 && mod100 !== 11) return `${days} день`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${days} дня`;
  return `${days} дней`;
}

export function BackLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="adm-back" type="button" onClick={onClick}>
      {label}
    </button>
  );
}

export function ClientList({ onOpenClient }: { onOpenClient: (id: number) => void }) {
  const [items, setItems] = useState<ClientRow[] | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    void adminFetch<{ items: ClientRow[] }>("/clients")
      .then((data) => setItems(data.items))
      .catch(() => setItems([]));
  }, []);

  if (items === null) return null;
  if (!items.length) return <p className="adm-empty">Клиентов пока нет.</p>;

  // Поиск по имени и контактам. При сотнях клиентов список без него
  // неприменим, а на сервере фильтрации нет.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? items.filter((client) =>
        `${client.full_name ?? ""} ${contactLine(client)}`.toLowerCase().includes(needle),
      )
    : items;

  return (
    <>
      <input
        className="adm-search"
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Поиск по имени или контакту"
        aria-label="Поиск по клиентам"
      />
      {shown.length === 0 ? (
        <p className="adm-empty">Никто не найден по запросу «{query.trim()}».</p>
      ) : (
        <div className="adm-list">
          {shown.map((client) => (
            <Row
              key={client.id}
              title={client.full_name || "Без имени"}
              subtitle={contactLine(client)}
              badge={`брифов: ${client.brief_count}`}
              onClick={() => onOpenClient(client.id)}
            />
          ))}
        </div>
      )}
    </>
  );
}

export function BriefList({
  status,
  onOpenBrief,
}: {
  status: "recent" | "pending";
  onOpenBrief: (id: number) => void;
}) {
  const [items, setItems] = useState<BriefListItem[] | null>(null);

  // Сброс при смене статуса делает родитель через key={status} — компонент
  // перемонтируется, поэтому обнулять состояние вручную не нужно.
  useEffect(() => {
    void adminFetch<{ items: BriefListItem[] }>(`/briefs?status=${status}`)
      .then((data) => setItems(data.items))
      .catch(() => setItems([]));
  }, [status]);

  if (items === null) return null;
  if (!items.length) {
    return (
      <p className="adm-empty">
        {status === "pending" ? "Никто не ждёт заполнения." : "За неделю брифов не приходило."}
      </p>
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
            // Срок ожидания приходил с сервера и нигде не показывался, хотя
            // именно он определяет, кому писать первым.
            extra={
              item.waiting_days > 0
                ? { text: daysLabel(item.waiting_days), kind: "wait" }
                : undefined
            }
            badge={item.brief_id ? "открыть" : "ждём"}
            badgeKind={item.brief_id ? "accent" : undefined}
            onClick={item.brief_id ? () => onOpenBrief(item.brief_id as number) : undefined}
          />
        );
      })}
    </div>
  );
}

export function CampaignList() {
  const [items, setItems] = useState<CampaignRow[] | null>(null);

  useEffect(() => {
    void adminFetch<{ items: CampaignRow[] }>("/campaigns")
      .then((data) => setItems(data.items))
      .catch(() => setItems([]));
  }, []);

  if (items === null) return null;
  if (!items.length) return <p className="adm-empty">Кампаний пока нет.</p>;

  return (
    <div className="adm-list">
      {items.map((campaign) => (
        <Row
          key={campaign.id}
          title={`Кампания №${campaign.id} · ${campaign.client_name || "—"}`}
          subtitle={`бриф №${campaign.brief_id} · ${campaign.objective}`}
          badge={STATUS_RU[campaign.status] ?? campaign.status}
          badgeKind={campaign.status === "launched" ? "accent" : undefined}
        />
      ))}
    </div>
  );
}

export function ClientDetailView({
  id,
  onBack,
  onOpenBrief,
}: {
  id: number;
  onBack: () => void;
  onOpenBrief: (briefId: number) => void;
}) {
  const [client, setClient] = useState<ClientDetail | null>(null);

  useEffect(() => {
    void adminFetch<ClientDetail>(`/clients/${id}`)
      .then(setClient)
      .catch(() => setClient(null));
  }, [id]);

  if (!client) return null;

  return (
    <>
      <BackLink label="← к клиентам" onClick={onBack} />
      <div className="card">
        <h3>{client.full_name || "Клиент"}</h3>
        <p>
          {[client.email, client.phone, client.telegram].filter(Boolean).map((line, index) => (
            <span key={line}>
              {index > 0 ? <br /> : null}
              {line}
            </span>
          ))}
        </p>
      </div>
      <div className="section-title">Брифы</div>
      {client.briefs.length ? (
        client.briefs.map((brief) => (
          <Row
            key={brief.id}
            title={`Бриф №${brief.id} · ${VARIANT_RU[brief.variant] ?? brief.variant}`}
            badge={STATUS_RU[brief.status] ?? brief.status}
            onClick={() => onOpenBrief(brief.id)}
          />
        ))
      ) : (
        <p className="note">Брифов нет.</p>
      )}
    </>
  );
}
