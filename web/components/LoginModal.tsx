"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

type LoginMode = "pw" | "link";

/**
 * Модалка входа в кабинет.
 *
 * Перенос прежнего инлайнового скрипта лендинга без потерь по доступности
 * (WAI-ARIA APG): ловушка фокуса внутри карточки, Esc закрывает и возвращает
 * фокус на элемент-открыватель, `aria-modal` + `aria-labelledby`, submit по
 * Enter, `aria-pressed` на сегментном переключателе и на кнопке показа пароля.
 *
 * Открывается кликом по любому элементу с `data-open-login` (они разбросаны по
 * странице: шапка, герой, финальный CTA) и автоматически при `?login=1` —
 * туда отправляет кабинет, если сессии нет.
 */
export function LoginModal() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<LoginMode>("pw");
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [sendingLink, setSendingLink] = useState(false);
  const [linkMsg, setLinkMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const cardRef = useRef<HTMLDivElement>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);

  const openModal = useCallback(() => {
    lastFocusedRef.current = document.activeElement as HTMLElement | null;
    setError("");
    setMode("pw");
    setOpen(true);
  }, []);

  const closeModal = useCallback(() => {
    setOpen(false);
    lastFocusedRef.current?.focus?.();
  }, []);

  // Открыватели раскиданы по статической разметке страницы — вешаемся на них
  // так же, как прежний скрипт: по атрибуту, а не по конкретным id.
  useEffect(() => {
    const openers = Array.from(document.querySelectorAll<HTMLElement>("[data-open-login]"));
    const onClick = (event: Event) => {
      event.preventDefault();
      openModal();
    };
    openers.forEach((el) => el.addEventListener("click", onClick));
    return () => openers.forEach((el) => el.removeEventListener("click", onClick));
  }, [openModal]);

  // Открыть сразу по ?login=1 (редирект из кабинета без сессии).
  // Эффект здесь неизбежен: страница пререндерится статически, и начальное
  // состояние на сервере обязано быть «закрыто» — иначе разъедется гидратация.
  // Прочитать location можно только после монтирования, отсюда одно-разовый
  // setState на старте.
  useEffect(() => {
    if (new URLSearchParams(location.search).get("login") === "1") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      openModal();
    }
  }, [openModal]);

  // Блокировка прокрутки фона + Esc.
  useEffect(() => {
    if (!open) return;

    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeModal();
    };
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, closeModal]);

  // Фокус на первое поле выбранного способа входа.
  useEffect(() => {
    if (!open) return;
    const id = mode === "pw" ? "lm-email" : "lm-forgot-email";
    const timer = setTimeout(() => document.getElementById(id)?.focus(), 20);
    return () => clearTimeout(timer);
  }, [open, mode]);

  /** Ловушка фокуса: Tab ходит по кругу внутри карточки, не уходя на фон. */
  function handleCardKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Tab" || !cardRef.current) return;

    const focusable = Array.from(
      cardRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
      // Скрытая панель (второй способ входа) в обход не участвует.
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

  async function handlePasswordSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    const form = event.currentTarget;
    const email = (form.elements.namedItem("email") as HTMLInputElement).value.trim();
    const password = (form.elements.namedItem("password") as HTMLInputElement).value;

    if (!email || !password) {
      setError("Введите email и пароль.");
      return;
    }

    setLoggingIn(true);
    try {
      await apiFetch("/cabinet/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      location.href = "/cabinet.html";
      return;
    } catch (err) {
      if (err instanceof ApiError) {
        setError(
          err.status === 429
            ? "Слишком много попыток. Подождите минуту и попробуйте снова."
            : "Неверный email или пароль.",
        );
      } else {
        setError("Не удалось связаться с сервером. Проверьте соединение.");
      }
    }
    setLoggingIn(false);
  }

  async function handleLinkSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const form = event.currentTarget;
    const email = (form.elements.namedItem("email") as HTMLInputElement).value.trim();
    setLinkMsg(null);
    if (!email) return;

    setSendingLink(true);
    let ok = false;
    try {
      await apiFetch("/cabinet/request-link", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      ok = true;
    } catch {
      ok = false;
    }

    // Ответ намеренно не раскрывает, есть ли такой клиент.
    setLinkMsg({
      ok,
      text: ok
        ? "Если аккаунт с таким email есть, мы отправили ссылку для входа."
        : "Не удалось отправить. Попробуйте позже.",
    });
    setSendingLink(false);
  }

  return (
    <div
      className="lp-modal"
      id="login-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="lm-title"
      hidden={!open}
    >
      <div className="lp-modal__backdrop" onClick={closeModal}></div>

      <div className="lp-modal__card" ref={cardRef} onKeyDown={handleCardKeyDown}>
        <button
          className="lp-modal__close"
          type="button"
          aria-label="Закрыть окно"
          onClick={closeModal}
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

        <div className="lp-modal__head">
          <span className="lp-badge lp-modal__badge" aria-hidden="true">
            ВК
          </span>
          <span className="lp-modal__kicker">
            <span className="lp-modal__live" aria-hidden="true"></span>Личный кабинет
          </span>
          <h2 className="lp-modal__title" id="lm-title">
            Вход в кабинет
          </h2>
          <p className="lp-modal__sub">
            Статус ваших брифов и запущенных кампаний — в одном месте.
          </p>
        </div>

        {/* Два реальных способа входа: по паролю или по ссылке на почту. */}
        <div className="lp-seg" role="group" aria-label="Способ входа">
          <button
            className={mode === "pw" ? "lp-seg__btn is-active" : "lp-seg__btn"}
            type="button"
            aria-pressed={mode === "pw"}
            data-login-mode="pw"
            onClick={() => setMode("pw")}
          >
            По паролю
          </button>
          <button
            className={mode === "link" ? "lp-seg__btn is-active" : "lp-seg__btn"}
            type="button"
            aria-pressed={mode === "link"}
            data-login-mode="link"
            onClick={() => setMode("link")}
          >
            По ссылке на почту
          </button>
        </div>

        {/* Способ 1 — email + пароль. */}
        <form
          className="lp-modal__pane"
          id="lm-form-pw"
          noValidate
          hidden={mode !== "pw"}
          onSubmit={handlePasswordSubmit}
        >
          <div className="form-field">
            <label htmlFor="lm-email">Email</label>
            <input
              id="lm-email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              required
            />
          </div>
          <div className="form-field">
            <label htmlFor="lm-password">Пароль</label>
            <div className="lp-input-wrap">
              <input
                id="lm-password"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                placeholder="Ваш пароль"
                required
              />
              <button
                className={showPassword ? "lp-eye is-on" : "lp-eye"}
                type="button"
                aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                aria-pressed={showPassword}
                data-toggle-password="lm-password"
                onClick={() => setShowPassword((value) => !value)}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"
                    stroke="currentColor"
                    strokeWidth="1.8"
                  />
                  <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
                </svg>
              </button>
            </div>
          </div>
          <div className="lp-modal__error" id="lm-error" role="alert" hidden={!error}>
            {error}
          </div>
          <button
            className={
              loggingIn
                ? "lp-btn lp-btn--primary lp-modal__submit is-loading"
                : "lp-btn lp-btn--primary lp-modal__submit"
            }
            id="lm-login"
            type="submit"
            disabled={loggingIn}
          >
            {loggingIn ? "Входим…" : "Войти"}
          </button>
        </form>

        {/* Способ 2 — ссылка для входа на почту (первый вход / забытый пароль). */}
        <form
          className="lp-modal__pane"
          id="lm-form-link"
          noValidate
          hidden={mode !== "link"}
          onSubmit={handleLinkSubmit}
        >
          <p className="lp-modal__hint">
            Пришлём ссылку для входа на вашу почту — подойдёт, если вы ещё не задавали пароль или
            забыли его.
          </p>
          <div className="form-field">
            <label htmlFor="lm-forgot-email">Email</label>
            <input
              id="lm-forgot-email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              required
            />
          </div>
          <div
            className={
              linkMsg ? `lp-modal__msg ${linkMsg.ok ? "is-ok" : "is-err"}` : "lp-modal__msg"
            }
            id="lm-forgot-msg"
            role="status"
            hidden={!linkMsg}
          >
            {linkMsg?.text}
          </div>
          <button
            className={
              sendingLink
                ? "lp-btn lp-btn--outline lp-modal__submit is-loading"
                : "lp-btn lp-btn--outline lp-modal__submit"
            }
            id="lm-forgot-send"
            type="submit"
            disabled={sendingLink}
          >
            {sendingLink ? "Отправляем…" : "Прислать ссылку"}
          </button>
        </form>

        <p className="lp-modal__foot">
          Ещё не отправляли бриф? <a href="/brief-individual.html">Заполнить бриф&nbsp;→</a>
        </p>
      </div>
    </div>
  );
}
