"use client";

// Шаг 1 мастера — рекламный кабинет (spec 2026-08-31-admin-cabinet-design-brief.md §5).
// Порядок повторяет бот один в один (bot/handlers/creative.py:
// `offer_cabinet_creation`/`_continue_cabinet_choice`): сперва — если карточка
// брифа разрешает — шаг «завести клиенту кабинет автоматически», и только
// затем список кабинетов для явного выбора.

import { useEffect, useState } from "react";

import {
  adminFetch,
  agencyCabinetErrorMessage,
  type AdAccount,
  type BriefCard,
  type Flash,
} from "@/lib/adminApi";
import { routeHash } from "@/lib/adminRoute";
import { useAdminResource } from "@/lib/useAdminResource";

import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { SkeletonRows } from "../ui/Skeleton";

const NO_CABINETS = "Ни одного рекламного кабинета не добавлено — запускать некуда.";
const NO_LIVE_CABINETS = "Ни один кабинет сейчас не годится: VK не принимает их токены.";
const MISSING_NAME =
  "У клиента не указано имя или название — автоматически создать кабинет не получится. " +
  "Дособерите данные правкой брифа. Пока продолжаем с общим кабинетом, если он есть.";
const MISSING_TAX_ID =
  "У клиента не указан ИНН, поэтому отдельный кабинет пока не завести — так требует " +
  "закон о рекламе. Дособерите ИНН правкой брифа, тогда кабинет можно будет создать " +
  "автоматически. Пока продолжаем с общим кабинетом, если он есть.";

/** ИНН клиента из брифа — та же логика, что `_tax_id` в боте (bot/handlers/creative.py):
 * подпись поля разная у вариантов брифа, но обе начинаются с «ИНН». */
function taxIdFromCard(card: BriefCard): string {
  for (const field of card.fields) {
    if (field.label.startsWith("ИНН")) return field.value;
  }
  return "";
}

function accountLine(account: AdAccount): string {
  if (account.client_id != null) {
    return account.client_name
      ? `Закреплён за клиентом: ${account.client_name}`
      : "Закреплён за клиентом (имя не указано)";
  }
  return "Кабинет общий — доступен любому клиенту";
}

function goToCabinets(): void {
  window.location.hash = routeHash.channels();
}

export function CabinetStep({
  brief_id,
  card,
  onCardUpdate,
  onFlash,
  onPicked,
}: {
  brief_id: number;
  card: BriefCard;
  onCardUpdate: (card: BriefCard) => void;
  onFlash: (flash: Flash) => void;
  onPicked: (account: AdAccount) => void;
}) {
  const clientId = card.client.id;
  const [state, retry] = useAdminResource<{ items: AdAccount[] }>(
    clientId != null ? `/ad-accounts?client_id=${clientId}` : "/ad-accounts",
    [clientId],
  );

  // Предложение завести кабинет — показывается один раз за визит на этот шаг;
  // после решения (создать либо выбрать вручную) список остаётся видимым, даже
  // если карточка брифа не изменилась (те же предусловия — тот же результат).
  const offerAvailable =
    Boolean(card.cabinet_step_available) &&
    !card.cabinet_step_own_cabinet_exists &&
    !card.cabinet_step_blocked_reason;
  const [offerDismissed, setOfferDismissed] = useState(false);
  const [creating, setCreating] = useState(false);

  const usable = state.status === "ready" ? state.data.items.filter((a) => a.is_usable) : [];

  // Единственный годный кабинет — выбирать не из чего, но карточка запуска на
  // следующем шаге всё равно покажет, куда именно уедет кампания (та же логика,
  // что в боте: `bot/handlers/creative.py::_continue_cabinet_choice`).
  useEffect(() => {
    if (state.status === "ready" && usable.length === 1 && (!offerAvailable || offerDismissed)) {
      onPicked(usable[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status, usable.length, offerAvailable, offerDismissed]);

  async function createCabinet() {
    setCreating(true);
    try {
      await adminFetch<AdAccount>("/ad-accounts/agency-cabinets", {
        method: "POST",
        body: JSON.stringify({
          client_id: clientId,
          full_name: card.client.full_name || "",
          tax_id: taxIdFromCard(card),
        }),
      });
      onFlash({ text: "Кабинет создан и подключён.", ok: true });
      const freshCard = await adminFetch<BriefCard>(`/briefs/${brief_id}`);
      onCardUpdate(freshCard);
      setOfferDismissed(true);
      retry();
    } catch (error) {
      onFlash({ text: agencyCabinetErrorMessage(error), ok: false });
    } finally {
      setCreating(false);
    }
  }

  if (offerAvailable && !offerDismissed) {
    const fullName = card.client.full_name || "не указано";
    return (
      <div className="adm-panel">
        <p className="wiz-offer__title">У клиента ещё нет своего рекламного кабинета</p>
        <p>
          Заведём его в вашем агентстве VK Рекламы — и реклама клиента будет идти именно с него,
          отдельно от общих кабинетов.
        </p>
        <dl className="adm-fields adm-fields--plain">
          <div className="adm-field">
            <dd className="adm-field__label">Рекламодатель</dd>
            <dd className="adm-field__value">{fullName}</dd>
          </div>
          <div className="adm-field">
            <dd className="adm-field__label">ИНН</dd>
            <dd className="adm-field__value">{taxIdFromCard(card) || "не указан"}</dd>
          </div>
          <div className="adm-field">
            <dd className="adm-field__label">Кабинет назовём</dd>
            <dd className="adm-field__value">«{fullName}»</dd>
          </div>
        </dl>
        <p className="adm-panel__hint">
          Можно создать кабинет сейчас либо выбрать кабинет вручную, как раньше.
        </p>
        <div className="adm-actions">
          <button
            className="btn btn--primary"
            type="button"
            disabled={creating}
            onClick={() => void createCabinet()}
          >
            {creating ? "Создаём…" : "Создать кабинет"}
          </button>
          <button
            className="btn"
            type="button"
            disabled={creating}
            onClick={() => setOfferDismissed(true)}
          >
            Выбрать кабинет вручную
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      {card.cabinet_step_blocked_reason === "missing_name" ? (
        <p className="note">{MISSING_NAME}</p>
      ) : null}
      {card.cabinet_step_blocked_reason === "missing_tax_id" ? (
        <p className="note">{MISSING_TAX_ID}</p>
      ) : null}

      {state.status === "loading" ? <SkeletonRows count={3} /> : null}
      {state.status === "error" ? (
        <ErrorState message="Не удалось загрузить рекламные кабинеты." onRetry={retry} />
      ) : null}
      {state.status === "ready" && !state.data.items.length ? (
        <EmptyState
          title={NO_CABINETS}
          action={{ label: "Кабинеты и каналы", onClick: goToCabinets }}
        />
      ) : null}
      {state.status === "ready" && state.data.items.length > 0 && !usable.length ? (
        <EmptyState
          title={NO_LIVE_CABINETS}
          action={{ label: "Кабинеты и каналы", onClick: goToCabinets }}
        />
      ) : null}

      {state.status === "ready" && usable.length > 1 ? (
        <div className="adm-list">
          {usable.map((account) => (
            <button
              className="adm-row"
              type="button"
              key={account.id}
              onClick={() => onPicked(account)}
            >
              <span className="adm-row__main">
                <span className="adm-row__title">
                  {account.title} <span className="adm-mono">id {account.external_id}</span>
                </span>
                <span className="adm-row__meta">
                  {accountLine(account)}
                  {account.balance_rub ? ` · баланс ${account.balance_rub} ₽` : ""}
                </span>
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
