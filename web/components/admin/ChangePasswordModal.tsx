"use client";

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { ApiError } from "@/lib/api";
import { adminFetch } from "@/lib/adminApi";

import { PasswordField } from "./ui/PasswordField";

/**
 * Смена пароля из шапки кабинета — та же пластика, что у входа (карточка,
 * заголовок, подзаголовок, кнопка во всю ширину), в виде диалога поверх
 * содержимого (ловушка фокуса, Esc закрывает — как в `LoginModal`).
 */
export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    const timer = setTimeout(() => cardRef.current?.querySelector("input")?.focus(), 20);
    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", onKeyDown);
      clearTimeout(timer);
    };
  }, [onClose]);

  /** Ловушка фокуса: Tab ходит по кругу внутри карточки (как в LoginModal). */
  function handleCardKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Tab" || !cardRef.current) return;
    const focusable = Array.from(
      cardRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (password.length < 10) {
      setError("Пароль короче десяти символов — так его слишком просто подобрать.");
      return;
    }
    if (password !== confirm) {
      setError("Пароли не совпадают.");
      return;
    }

    setBusy(true);
    try {
      await adminFetch("/password", { method: "POST", body: JSON.stringify({ password }) });
      setSuccess(true);
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "string") {
        setError(err.detail);
      } else {
        setError("Не получилось связаться с сервером. Проверьте связь и попробуйте снова.");
      }
    }
    setBusy(false);
  }

  return (
    <div className="adm-dialog" role="dialog" aria-modal="true" aria-labelledby="cp-title">
      <div className="adm-dialog__backdrop" onClick={onClose}></div>

      <div className="adm-dialog__card" ref={cardRef} onKeyDown={handleCardKeyDown}>
        <button
          className="adm-dialog__close"
          type="button"
          aria-label="Закрыть окно"
          onClick={onClose}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M6 6l12 12M18 6 6 18"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            />
          </svg>
        </button>

        <div className="adm-dialog__head">
          <h2 className="adm-dialog__title" id="cp-title">
            Сменить пароль
          </h2>
          <p className="adm-dialog__sub">Новый пароль потребуется при следующем входе.</p>
        </div>

        {success ? (
          <>
            <div className="result show ok">Пароль обновлён.</div>
            <button className="btn btn--primary adm-dialog__submit" type="button" onClick={onClose}>
              Готово
            </button>
          </>
        ) : (
          <form noValidate onSubmit={(event) => void handleSubmit(event)}>
            <PasswordField
              label="Новый пароль"
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
            />
            <PasswordField
              label="Повторите пароль"
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
            />
            <div className="adm-auth__error" role="alert" hidden={!error}>
              {error}
            </div>
            <button
              className={
                busy
                  ? "btn btn--primary adm-dialog__submit is-loading"
                  : "btn btn--primary adm-dialog__submit"
              }
              type="submit"
              disabled={busy}
            >
              {busy ? "Сохраняем…" : "Сохранить"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
