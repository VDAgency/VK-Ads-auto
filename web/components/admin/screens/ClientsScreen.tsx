"use client";

import { useState } from "react";

import { contactLine, STATUS_RU, type ClientDetail, type ClientRow } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { SendBrief } from "../SendBrief";
import { BackLink } from "../ui/BackLink";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { SkeletonCard, SkeletonRows } from "../ui/Skeleton";

function ClientList({ onOpenClient }: { onOpenClient: (id: number) => void }) {
  const [state, retry] = useAdminResource<{ items: ClientRow[] }>("/clients");
  const [query, setQuery] = useState("");
  const [showSend, setShowSend] = useState(false);

  return (
    <>
      <div className="adm-toolbar">
        <input
          className="adm-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Поиск по имени или контакту"
          aria-label="Поиск по клиентам"
        />
        <button className="btn" type="button" onClick={() => setShowSend((value) => !value)}>
          {showSend ? "Свернуть" : "Отправить бриф клиенту"}
        </button>
      </div>

      {showSend ? <SendBrief /> : null}

      {state.status === "loading" ? <SkeletonRows /> : null}
      {state.status === "error" ? <ErrorState onRetry={retry} /> : null}
      {state.status === "ready" ? (
        <ClientListReady items={state.data.items} query={query} onOpenClient={onOpenClient} />
      ) : null}
    </>
  );
}

function ClientListReady({
  items,
  query,
  onOpenClient,
}: {
  items: ClientRow[];
  query: string;
  onOpenClient: (id: number) => void;
}) {
  if (!items.length) {
    return (
      <EmptyState
        title="Клиентов пока нет."
        description="Они появятся здесь после первой отправки брифа."
      />
    );
  }

  // Поиск по имени и контактам — на сервере фильтрации нет, при сотнях
  // клиентов список без него неприменим.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? items.filter((client) =>
        `${client.full_name ?? ""} ${contactLine(client)}`.toLowerCase().includes(needle),
      )
    : items;

  if (!shown.length) {
    return <EmptyState title={`Никто не найден по запросу «${query.trim()}».`} />;
  }

  return (
    <div className="adm-list">
      {shown.map((client) => (
        <Row
          key={client.id}
          title={client.full_name || "Без имени"}
          subtitle={contactLine(client)}
          badge={<span className="badge">брифов: {client.brief_count}</span>}
          onClick={() => onOpenClient(client.id)}
        />
      ))}
    </div>
  );
}

function ClientDetailView({
  id,
  onBack,
  onOpenBrief,
}: {
  id: number;
  onBack: () => void;
  onOpenBrief: (briefId: number) => void;
}) {
  const [state, retry] = useAdminResource<ClientDetail>(`/clients/${id}`, [id]);

  return (
    <>
      <BackLink label="← к клиентам" onClick={onBack} />
      {state.status === "loading" ? <SkeletonCard /> : null}
      {state.status === "error" ? (
        <ErrorState message="Не удалось открыть клиента." onRetry={retry} />
      ) : null}
      {state.status === "ready" ? (
        <>
          <div className="card">
            <h2>{state.data.full_name || "Клиент"}</h2>
            <p>
              {[state.data.email, state.data.phone, state.data.telegram]
                .filter(Boolean)
                .map((line, index) => (
                  <span key={line}>
                    {index > 0 ? <br /> : null}
                    {line}
                  </span>
                ))}
            </p>
          </div>
          <h2 className="section-title">Брифы</h2>
          {state.data.briefs.length ? (
            <div className="adm-list">
              {state.data.briefs.map((brief) => (
                <Row
                  key={brief.id}
                  title={`Бриф №${brief.id}`}
                  badge={<span className="badge">{STATUS_RU[brief.status] ?? brief.status}</span>}
                  onClick={() => onOpenBrief(brief.id)}
                />
              ))}
            </div>
          ) : (
            <EmptyState title="Брифов нет." description="Этот клиент ещё не присылал бриф." />
          )}
        </>
      ) : null}
    </>
  );
}

export function ClientsScreen({
  clientId,
  onOpenClient,
  onCloseClient,
  onOpenBrief,
}: {
  clientId: number | null;
  onOpenClient: (id: number) => void;
  onCloseClient: () => void;
  onOpenBrief: (briefId: number) => void;
}) {
  if (clientId != null) {
    return <ClientDetailView id={clientId} onBack={onCloseClient} onOpenBrief={onOpenBrief} />;
  }
  return <ClientList onOpenClient={onOpenClient} />;
}
