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
  onClick,
}: {
  title: string;
  subtitle?: string;
  badge: string;
  onClick?: () => void;
}) {
  return (
    <button className="adm-row" type="button" onClick={onClick}>
      <div>
        <strong>{title}</strong>
        {subtitle ? <div className="muted">{subtitle}</div> : null}
      </div>
      <span className="adm-badge">{badge}</span>
    </button>
  );
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

  useEffect(() => {
    void adminFetch<{ items: ClientRow[] }>("/clients")
      .then((data) => setItems(data.items))
      .catch(() => setItems([]));
  }, []);

  if (items === null) return null;
  if (!items.length) return <p className="note">Клиентов пока нет.</p>;

  return (
    <>
      {items.map((client) => (
        <Row
          key={client.id}
          title={client.full_name || "Без имени"}
          subtitle={contactLine(client)}
          badge={`брифов: ${client.brief_count}`}
          onClick={() => onOpenClient(client.id)}
        />
      ))}
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
  if (!items.length) return <p className="note">Пусто.</p>;

  return (
    <>
      {items.map((item, index) => {
        const who = item.contact_name ? `${item.contact_name} — ${item.contact}` : item.contact;
        return (
          <Row
            key={`${item.contact}-${index}`}
            title={who}
            subtitle={`${VARIANT_RU[item.variant] ?? item.variant} · ${item.channel}`}
            badge={item.brief_id ? "открыть" : "ждём"}
            onClick={item.brief_id ? () => onOpenBrief(item.brief_id as number) : undefined}
          />
        );
      })}
    </>
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
  if (!items.length) return <p className="note">Кампаний пока нет.</p>;

  return (
    <>
      {items.map((campaign) => (
        <Row
          key={campaign.id}
          title={`Кампания №${campaign.id} · ${campaign.client_name || "—"}`}
          subtitle={`бриф №${campaign.brief_id} · ${campaign.objective}`}
          badge={STATUS_RU[campaign.status] ?? campaign.status}
        />
      ))}
    </>
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
