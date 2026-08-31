"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { AD_ACCOUNT_ERRORS, adminFetch, HEALTH_RU, type AdAccount } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { HealthBadge } from "./ui/Badge";
import { EmptyState } from "./ui/EmptyState";
import { ErrorState } from "./ui/ErrorState";
import { SkeletonRows } from "./ui/Skeleton";

/**
 * Рекламные кабинеты оператора: список, добавление, проверка, удаление.
 *
 * Веб-зеркало команды `/cabinets` в боте — оба ходят в один и тот же сервис
 * ядра, поэтому картина одинаковая (spec 2026-07-27 §11).
 *
 * Токен вводится в поле типа `password`, уходит одним запросом и в состоянии
 * не задерживается: обратно ядро отдаёт только последние 4 символа.
 */
export function AdAccounts() {
  const [state, retry] = useAdminResource<{ items: AdAccount[] }>("/ad-accounts");
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const [showForm, setShowForm] = useState(false);
  const [token, setToken] = useState("");
  const [kind, setKind] = useState<"owner" | "third_party">("owner");
  const [advertiserName, setAdvertiserName] = useState("");
  const [advertiserInn, setAdvertiserInn] = useState("");

  async function add() {
    const value = token.trim();
    if (!value) {
      setMsg({ text: "Вставьте access_token кабинета.", ok: false });
      return;
    }
    setBusy(true);
    try {
      const created = await adminFetch<AdAccount>("/ad-accounts", {
        method: "POST",
        body: JSON.stringify({
          token: value,
          advertiser_kind: kind,
          advertiser_name: kind === "third_party" ? advertiserName.trim() || null : null,
          advertiser_inn: kind === "third_party" ? advertiserInn.trim() || null : null,
        }),
      });
      // Токен не держим в состоянии дольше отправки.
      setToken("");
      setAdvertiserName("");
      setAdvertiserInn("");
      setShowForm(false);
      setMsg({ text: `✅ Кабинет «${created.title}» добавлен.`, ok: true });
      retry();
    } catch (error) {
      const detail = error instanceof ApiError ? String(error.detail ?? "") : "";
      setMsg({
        text: AD_ACCOUNT_ERRORS[detail] ?? "Не получилось добавить кабинет. Проверьте токен.",
        ok: false,
      });
    } finally {
      setBusy(false);
    }
  }

  async function check(id: number) {
    setBusy(true);
    try {
      const updated = await adminFetch<AdAccount>(`/ad-accounts/${id}/check`, { method: "POST" });
      setMsg({
        text: `Кабинет «${updated.title}»: ${HEALTH_RU[updated.health] ?? updated.health}`,
        ok: updated.health === "healthy",
      });
      retry();
    } catch {
      setMsg({ text: "Не удалось проверить кабинет.", ok: false });
    } finally {
      setBusy(false);
    }
  }

  async function remove(account: AdAccount) {
    const confirmed = window.confirm(
      `Удалить кабинет «${account.title}»?\n\n` +
        "Сохранённый токен будет стёрт безвозвратно. Кампании, запущенные в нём, останутся в отчётах.",
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      await adminFetch(`/ad-accounts/${account.id}`, { method: "DELETE" });
      setMsg({ text: "🗑 Кабинет удалён.", ok: true });
      retry();
    } catch {
      setMsg({ text: "Не удалось удалить кабинет.", ok: false });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="adm-nav adm-nav--actions">
        <button
          className="btn btn--primary"
          type="button"
          onClick={() => setShowForm((open) => !open)}
        >
          {showForm ? "Свернуть" : "Добавить кабинет"}
        </button>
      </div>

      {showForm ? (
        <div className="adm-card">
          <p className="note">
            Нужен <strong>access_token</strong> кабинета: в VK Рекламе «Профиль» → «Доступ к API».
            Название и номер кабинета подтянутся из VK автоматически.
          </p>

          <div className="form-field">
            <label htmlFor="ad-account-token">access_token</label>
            <input
              id="ad-account-token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="Вставьте токен"
            />
          </div>

          <fieldset className="form-field">
            <legend>Чью рекламу размещаем</legend>
            <label>
              <input
                type="radio"
                name="advertiser_kind"
                checked={kind === "owner"}
                onChange={() => setKind("owner")}
              />{" "}
              Свою
            </label>{" "}
            <label>
              <input
                type="radio"
                name="advertiser_kind"
                checked={kind === "third_party"}
                onChange={() => setKind("third_party")}
              />{" "}
              Третьего лица
            </label>
          </fieldset>

          {kind === "third_party" ? (
            <>
              <div className="form-field">
                <label htmlFor="ad-account-advertiser">Конечный рекламодатель</label>
                <input
                  id="ad-account-advertiser"
                  type="text"
                  value={advertiserName}
                  onChange={(event) => setAdvertiserName(event.target.value)}
                  placeholder="ООО «Ромашка»"
                />
              </div>
              <div className="form-field">
                <label htmlFor="ad-account-inn">ИНН</label>
                <input
                  id="ad-account-inn"
                  type="text"
                  inputMode="numeric"
                  value={advertiserInn}
                  onChange={(event) => setAdvertiserInn(event.target.value)}
                  placeholder="7701234567"
                />
              </div>
            </>
          ) : null}

          <button
            className="btn btn--primary"
            type="button"
            disabled={busy}
            onClick={() => void add()}
          >
            Добавить
          </button>
        </div>
      ) : null}

      {msg ? <div className={`result show ${msg.ok ? "ok" : "err"}`}>{msg.text}</div> : null}

      {state.status === "loading" ? <SkeletonRows count={3} /> : null}
      {state.status === "error" ? (
        <ErrorState message="Не удалось загрузить кабинеты." onRetry={retry} />
      ) : null}
      {state.status === "ready" && !state.data.items.length ? (
        <EmptyState
          title="Кабинетов пока нет."
          description="Пока не добавлен ни один, запускать кампании некуда."
        />
      ) : null}

      {state.status === "ready"
        ? state.data.items.map((account) => (
            <div className="adm-row adm-row--static" key={account.id}>
              <div>
                <strong>{account.title}</strong> <HealthBadge health={account.health} />
                <div className="muted">
                  id {account.external_id} · токен{" "}
                  {account.token_tail ? `…${account.token_tail}` : "—"}
                </div>
                {account.health_error ? <div className="muted">{account.health_error}</div> : null}
                {account.advertiser_kind === "third_party" ? (
                  <div className="muted">
                    реклама третьего лица: {account.advertiser_name || "не указан"}
                    {account.advertiser_inn ? `, ИНН ${account.advertiser_inn}` : ""}
                  </div>
                ) : null}
                {account.balance_rub ? (
                  <div className="muted">баланс {account.balance_rub} ₽</div>
                ) : null}
              </div>
              <div>
                <button
                  className="btn"
                  type="button"
                  disabled={busy}
                  onClick={() => void check(account.id)}
                >
                  Проверить
                </button>{" "}
                <button
                  className="btn"
                  type="button"
                  disabled={busy}
                  onClick={() => void remove(account)}
                >
                  Удалить
                </button>
              </div>
            </div>
          ))
        : null}
    </>
  );
}
