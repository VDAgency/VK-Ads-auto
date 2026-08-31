"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { adminFetch } from "@/lib/adminApi";

import { PasswordField } from "./ui/PasswordField";

/**
 * Вход в панель оператора: номер в Telegram + пароль (spec 2026-08-31 §3).
 *
 * Пластика — эталон `LoginModal.tsx` (карточка, бейдж, кикер, заголовок,
 * подзаголовок, кнопка во всю ширину), но не сама модалка: здесь это не
 * оверлей поверх страницы, а единственный экран, пока нет сессии.
 *
 * Старый вход по одноразовой ссылке (`?token=` → `/admin/authenticate`)
 * обрабатывается до этого компонента, в `page.tsx` — здесь только пароль.
 */
export function LoginScreen({
  notice,
  onLoggedIn,
}: {
  /** Сообщение над формой: магик-линк не сработал либо сессия закончилась
   * (`onSessionExpired` в `page.tsx`) — оба случая показываются тут же, чтобы
   * оператор понимал, почему его вернуло на вход, а не гадал. */
  notice: string;
  onLoggedIn: () => void;
}) {
  const [telegramId, setTelegramId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    const idValue = telegramId.trim();
    const idNumber = Number(idValue);
    if (!idValue || !Number.isFinite(idNumber) || !password) {
      setError("Введите номер в Telegram и пароль.");
      return;
    }

    setBusy(true);
    try {
      await adminFetch("/login", {
        method: "POST",
        body: JSON.stringify({ telegram_id: idNumber, password }),
      });
      onLoggedIn();
      return;
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.status === 429
            ? "Слишком много попыток. Подождите минуту."
            : "Не подходит номер или пароль.",
        );
      } else {
        setError("Не получилось связаться с сервером. Проверьте связь и попробуйте снова.");
      }
    }
    setBusy(false);
  }

  const shownError = error || notice;

  return (
    <main className="adm-auth">
      <div className="adm-auth__card">
        <span className="adm-auth__badge" aria-hidden="true">
          A
        </span>
        <span className="adm-auth__kicker">Панель оператора</span>
        <h1 className="adm-auth__title">
          Ads<span className="adm-brand__dot">·</span>auto
        </h1>
        <p className="adm-auth__sub">Брифы, кампании и кабинеты клиентов — в одном месте.</p>

        <form noValidate onSubmit={(event) => void handleSubmit(event)}>
          <div className="form-field">
            <label htmlFor="op-telegram-id">Ваш номер в Telegram</label>
            <input
              id="op-telegram-id"
              name="telegram_id"
              type="text"
              inputMode="numeric"
              autoComplete="username"
              placeholder="Например, 123456789"
              value={telegramId}
              onChange={(event) => setTelegramId(event.target.value)}
              required
            />
          </div>

          <PasswordField label="Пароль" value={password} onChange={setPassword} />

          <div className="adm-auth__error" role="alert" hidden={!shownError}>
            {shownError}
          </div>

          <button
            className={
              busy
                ? "btn btn--primary adm-auth__submit is-loading"
                : "btn btn--primary adm-auth__submit"
            }
            type="submit"
            disabled={busy}
          >
            {busy ? "Входим…" : "Войти"}
          </button>
        </form>

        <p className="adm-auth__hint">Пароль задаётся командой /set_password в боте.</p>
      </div>
    </main>
  );
}
