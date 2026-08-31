"use client";

// Шаг 4 мастера — подтверждение (spec 2026-08-31-admin-cabinet-design-brief.md §5, Т3).
// Кнопка «Запустить» живёт только здесь и нигде больше — та же карточка данных,
// что бот показывает в `render_launch_confirmation` (bot/handlers/creative.py),
// но собранная из полей `GET /briefs/{id}/launch-preview`, а не готовым текстом:
// сводку считает ядро, здесь только показ (плюс данные уже выбранного на прошлых
// шагах кабинета/цели/креатива — своей бизнес-логики тут нет).

import { useState } from "react";

import {
  adminFetch,
  launchErrorMessage,
  type AdAccount,
  type Flash,
  type LaunchOutcome,
  type LaunchPreview,
} from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { ErrorState } from "../ui/ErrorState";
import { SkeletonRows } from "../ui/Skeleton";
import { readFileBase64 } from "./fileUtils";
import type { PickedFile } from "./types";

/** Конечный рекламодатель кабинета — та же логика, что в списке кабинетов
 * (`AdAccounts.tsx`) и в карточке подтверждения бота
 * (`bot/handlers/creative.py::_advertiser_line`). Берётся из уже выбранного на
 * шаге «Кабинет» объекта — `launch-preview` эти поля не отдаёт (только
 * название и id кабинета). Отсутствие имени называется прямо — раньше строка
 * была голым «не указан» без объяснения, что именно не указано (найденный баг). */
function advertiserLine(account: AdAccount): string {
  if (account.advertiser_kind === "third_party") {
    if (!account.advertiser_name) return "Реклама третьего лица — рекламодатель не указан.";
    const inn = account.advertiser_inn ? `, ИНН ${account.advertiser_inn}` : "";
    return `Реклама третьего лица: ${account.advertiser_name}${inn}`;
  }
  return "владелец кабинета (реклама от своего имени)";
}

function bindingLine(preview: LaunchPreview): string {
  if (preview.ad_account_client_id == null) {
    return "Кабинет общий — доступен любому клиенту. Проверьте, что запускаете с нужного счёта.";
  }
  if (preview.ad_account_client_name) {
    return `Кабинет закреплён за этим клиентом: ${preview.ad_account_client_name}.`;
  }
  return "Кабинет закреплён за этим клиентом (имя не указано).";
}

export function ConfirmStep({
  brief_id,
  account,
  needsCreative,
  goalCode,
  goalTitle,
  picked,
  title,
  body,
  onFlash,
  onChangeCabinet,
  onLaunched,
}: {
  brief_id: number;
  account: AdAccount;
  needsCreative: boolean;
  goalCode: string | null;
  goalTitle: string | null;
  picked: PickedFile | null;
  title: string;
  body: string;
  onFlash: (flash: Flash) => void;
  onChangeCabinet: () => void;
  /** Итог запуска целиком, не только текст — родитель прячет кнопку «Запустить»
   * насовсем после успеха (не «серая», а её нет), а не просто блокирует её на
   * время запроса (найденный баг: повторное нажатие после успеха создавало
   * вторую кампанию по тому же брифу). */
  onLaunched: (outcome: LaunchOutcome) => void;
}) {
  const [state, retry] = useAdminResource<LaunchPreview>(
    `/briefs/${brief_id}/launch-preview?ad_account_id=${account.id}`,
    [account.id],
  );
  const [sending, setSending] = useState(false);

  if (state.status === "loading") return <SkeletonRows count={4} />;
  if (state.status === "error") {
    return <ErrorState message="Не удалось собрать сводку перед запуском." onRetry={retry} />;
  }

  const preview = state.data;
  // `launch-preview` всегда отдаёт цель запуска без креатива («Подписчики») —
  // для сценария с креативом показываем цель, которую оператор выбрал на шаге
  // «Цель рекламы» (уже подтверждённое ядром название из `GET /goals`), а не
  // подменяем её значением из предпросмотра.
  const goal = needsCreative ? goalTitle || "" : preview.goal_title;

  async function launch() {
    if (sending) return;
    setSending(true);
    try {
      let result: LaunchOutcome;
      if (needsCreative && picked) {
        const media_b64 = await readFileBase64(picked.file);
        result = await adminFetch<LaunchOutcome>(`/briefs/${brief_id}/creative`, {
          method: "POST",
          body: JSON.stringify({
            media_b64,
            media_type: picked.kind,
            width: picked.width,
            height: picked.height,
            title,
            body,
            ad_account_id: account.id,
            goal: goalCode,
          }),
        });
      } else {
        result = await adminFetch<LaunchOutcome>(`/briefs/${brief_id}/launch`, {
          method: "POST",
          body: JSON.stringify({ ad_account_id: account.id }),
        });
      }
      onLaunched(result);
    } catch (error) {
      onFlash({ text: launchErrorMessage(error), ok: false });
    } finally {
      setSending(false);
    }
  }

  return (
    <div>
      <dl className="adm-fields adm-fields--plain">
        <div className="adm-field">
          <dd className="adm-field__label">Клиент</dd>
          <dd className="adm-field__value">
            {preview.client_name || "не указан"} · ИНН {preview.client_tax_id || "не указан"}
          </dd>
        </div>
        <div className="adm-field">
          <dd className="adm-field__label">Объект рекламы</dd>
          <dd className="adm-field__value">{preview.object_url || "не указан"}</dd>
        </div>
        {preview.surface_title ? (
          <div className="adm-field">
            <dd className="adm-field__label">Площадка</dd>
            <dd className="adm-field__value">{preview.surface_title}</dd>
          </div>
        ) : null}
        <div className="adm-field">
          <dd className="adm-field__label">Цель</dd>
          <dd className="adm-field__value">{goal}</dd>
        </div>
        <div className="adm-field">
          <dd className="adm-field__label">Бюджет · срок</dd>
          <dd className="adm-field__value">
            {preview.budget_text || "не указан"} · {preview.term_text || "не указан"}
          </dd>
        </div>
        <div className="adm-field">
          <dd className="adm-field__label">Кабинет</dd>
          <dd className="adm-field__value">
            {preview.ad_account_title} (id {preview.ad_account_external_id})
            <br />
            {advertiserLine(account)}
            <br />
            {bindingLine(preview)}
            {preview.ad_account_balance_rub ? (
              <>
                <br />
                Баланс: {preview.ad_account_balance_rub} ₽
              </>
            ) : null}
          </dd>
        </div>
        {needsCreative ? (
          <div className="adm-field">
            <dd className="adm-field__label">Креатив</dd>
            <dd className="adm-field__value">
              {picked ? picked.file.name : "—"}
              {title ? (
                <>
                  <br />
                  Заголовок: {title}
                </>
              ) : null}
              {body ? (
                <>
                  <br />
                  Текст: {body}
                </>
              ) : null}
              {!title && !body ? (
                <>
                  <br />
                  Без описания.
                </>
              ) : null}
            </dd>
          </div>
        ) : null}
      </dl>

      {preview.balance_below_daily_budget ? (
        <p className="adm-warn">
          <strong>Баланс кабинета</strong> меньше дневного бюджета из брифа. Кампанию это не
          остановит, но кабинет стоит пополнить.
        </p>
      ) : null}

      {preview.client_mismatch ? (
        <>
          <div className="adm-drop__error" role="alert">
            Этот кабинет закреплён за другим клиентом — деньги спишутся не с того счёта. Выберите
            кабинет, закреплённый за клиентом брифа, либо общий.
          </div>
          <div className="adm-actions">
            <button className="btn" type="button" onClick={onChangeCabinet}>
              Выбрать другой кабинет
            </button>
          </div>
        </>
      ) : (
        <div className="adm-actions">
          <button
            className="btn btn--primary"
            type="button"
            disabled={sending}
            onClick={() => void launch()}
          >
            {sending ? "Запускаем…" : "Запустить"}
          </button>
        </div>
      )}
    </div>
  );
}
