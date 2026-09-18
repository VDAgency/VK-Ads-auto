"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import {
  adminFetch,
  contactLine,
  STATUS_RU,
  type ClientBankDetails,
  type ClientDetail,
  type ClientRow,
} from "@/lib/adminApi";
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

/** Реквизиты клиента — только на чтение (spec 2026-09-19 §E). Заполняет их сам
 * клиент в своём кабинете; оператору здесь править нечего. */
function BankDetailsCard({ details }: { details: ClientBankDetails | null }) {
  if (!details) {
    return (
      <div className="card">
        <h3>Реквизиты</h3>
        <p>Клиент ещё не заполнил.</p>
      </div>
    );
  }
  const rows: [string, string][] = [
    ["Плательщик", details.payer_name],
    ["Банк", details.bank_name],
    ["БИК", details.bik],
    ["Расчётный счёт", details.settlement_account],
    ["Корр. счёт", details.correspondent_account],
  ];
  return (
    <div className="card">
      <h3>Реквизиты</h3>
      <dl className="adm-bank">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
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
  const [revoking, setRevoking] = useState(false);
  const [revokeError, setRevokeError] = useState("");
  const [revoked, setRevoked] = useState(false);

  async function handleRevoke() {
    const confirmed = window.confirm(
      "Отозвать доступ клиенту? Он выйдет из кабинета на всех устройствах, " +
        "а выданные ему ссылки перестанут работать. Понадобится новая ссылка " +
        "или вход по паролю.",
    );
    if (!confirmed) return;

    setRevokeError("");
    setRevoking(true);
    try {
      await adminFetch(`/clients/${id}/revoke-access`, { method: "POST" });
      setRevoked(true);
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "string") {
        setRevokeError(err.detail);
      } else {
        setRevokeError("Не получилось связаться с сервером. Проверьте связь и попробуйте снова.");
      }
    }
    setRevoking(false);
  }

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
            <button
              className="btn"
              type="button"
              disabled={revoking || revoked}
              onClick={() => void handleRevoke()}
            >
              {revoked ? "Доступ отозван" : "Отозвать доступ"}
            </button>
            {revokeError ? (
              <div className="result show err" role="status">
                {revokeError}
              </div>
            ) : null}
          </div>
          <BankDetailsCard details={state.data.bank_details} />
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
