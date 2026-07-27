"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

import "./cabinet.css";

/**
 * Четыре шага пути брифа. Порядок значим: по нему строится дорожка статуса.
 * Подписи человеческие — клиент не обязан знать внутренние названия.
 */
const STAGES = [
  { key: "received", title: "Бриф получен", desc: "Мы приняли ваши ответы." },
  { key: "parsed", title: "Собираем кампанию", desc: "Из ответов готовятся настройки." },
  {
    key: "prepared",
    title: "Готово к запуску",
    desc: "Таргетолог проверяет настройки и подтверждает старт.",
  },
  { key: "launched", title: "Реклама запущена", desc: "Кампания работает." },
] as const;

const VARIANT_RU: Record<string, string> = {
  individual: "Частное лицо",
  community: "Бизнес",
};

type BriefStatus = { id: number; variant: string; status: string };

type CabinetView = {
  client_id: number;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  telegram: string | null;
  password_set: boolean;
  briefs: BriefStatus[];
  referral_url: string | null;
};

/** Вход — через модалку на главной; неавторизованных отправляем на неё. */
function redirectToLogin() {
  location.href = "/?login=1";
}

/** Первое имя из ФИО: «Здравствуйте, Мария» теплее, чем полное ФИО. */
function firstName(fullName: string | null): string | null {
  if (!fullName) return null;
  const parts = fullName.trim().split(/\s+/);
  return parts.length >= 2 ? parts[1] : parts[0];
}

function StatusTrack({ status }: { status: string }) {
  const currentIndex = STAGES.findIndex((stage) => stage.key === status);
  return (
    <ol className="track">
      {STAGES.map((stage, index) => {
        const state =
          currentIndex < 0 || index > currentIndex
            ? "is-todo"
            : index === currentIndex
              ? "is-current"
              : "is-done";
        return (
          <li className={`track__step ${state}`} key={stage.key}>
            <span className="track__dot" aria-hidden="true">
              {index + 1}
            </span>
            <div>
              <div className="track__title">{stage.title}</div>
              <p className="track__desc">{stage.desc}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function ReferralBlock({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // Буфер может быть недоступен — ссылка остаётся видимой и выделяемой.
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <section className="cab-card" aria-labelledby="ref-title">
      <h2 id="ref-title">Пригласите клиента — получите скидку</h2>
      <p className="cab-card__sub">
        Отправьте эту ссылку знакомому. Когда он заполнит бриф, мы это увидим и учтём скидку.
      </p>
      <div className="cab-copy">
        <input
          className="cab-copy__field"
          value={url}
          readOnly
          aria-label="Ваша реферальная ссылка"
        />
        <button className="btn btn--primary" type="button" onClick={() => void copy()}>
          {copied ? "Скопировано" : "Скопировать"}
        </button>
      </div>
      <p className="cab-copy__status" role="status">
        {copied ? "Ссылка скопирована в буфер обмена." : ""}
      </p>
    </section>
  );
}

export default function CabinetPage() {
  const [view, setView] = useState<CabinetView | null>(null);
  const [needPassword, setNeedPassword] = useState(false);
  const [error, setError] = useState("");
  const [setpwError, setSetpwError] = useState("");
  const [pw1, setPw1] = useState("");
  const [pw2, setPw2] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [email, setEmail] = useState<string | null>(null);

  /** Загрузить кабинет по session-cookie (без токена в URL). */
  const loadCabinet = useCallback(async () => {
    try {
      const data = await apiFetch<CabinetView>("/cabinet");
      setView(data);
      setNeedPassword(false);
      return true;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    async function init() {
      const token = new URLSearchParams(location.search).get("token");

      if (token) {
        try {
          const data = await apiFetch<CabinetView>(`/cabinet?token=${encodeURIComponent(token)}`);
          if (!data.password_set) {
            // Первый вход — обязательная установка пароля.
            setView(null);
            setEmail(data.email);
            setNeedPassword(true);
          } else {
            setView(data);
          }
        } catch {
          // Токен недействителен или истёк — на модалку входа.
          redirectToLogin();
        }
        return;
      }

      if (!(await loadCabinet())) {
        redirectToLogin();
      }
    }

    void init();
  }, [loadCabinet]);

  async function handleSetPassword() {
    setSetpwError("");

    if (pw1.length < 8) {
      setSetpwError("Пароль должен быть не короче 8 символов.");
      return;
    }
    if (pw1 !== pw2) {
      setSetpwError("Пароли не совпадают.");
      return;
    }

    const token = new URLSearchParams(location.search).get("token");
    try {
      await apiFetch("/cabinet/set-password", {
        method: "POST",
        body: JSON.stringify({ token, password: pw1 }),
      });
      // Пароль установлен, cookie-сессия выдана — открываем кабинет.
      if (!(await loadCabinet())) {
        setError("Пароль сохранён. Обновите страницу для входа.");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setSetpwError("Ссылка недействительна или истекла. Запросите новую.");
      } else {
        setSetpwError("Не удалось сохранить пароль. Попробуйте позже.");
      }
    }
  }

  async function handleLogout() {
    try {
      await apiFetch("/cabinet/logout", { method: "POST" });
    } catch {
      // Разлогин локально важнее, чем ответ сервера.
    }
    location.href = "/";
  }

  const contacts = [
    view?.email ? { label: "Почта", value: view.email } : null,
    view?.phone ? { label: "Телефон", value: view.phone } : null,
    view?.telegram ? { label: "Telegram", value: view.telegram } : null,
  ].filter((line): line is { label: string; value: string } => line !== null);

  const name = firstName(view?.full_name ?? null);

  return (
    <div className="cab">
      <a className="skip-link" href="#cab-main">
        Перейти к содержимому
      </a>

      <header className="cab-header">
        <div className="cab-header__inner">
          <Link className="cab-brand" href="/">
            Ads<span className="cab-brand__dot">·</span>auto
          </Link>
          {view ? (
            <button
              className="btn btn--ghost cab-logout"
              type="button"
              onClick={() => void handleLogout()}
            >
              Выйти
            </button>
          ) : null}
        </div>
      </header>

      <main id="cab-main" className="cab-main">
        {/* Первый вход: установка пароля */}
        {needPassword ? (
          <section className="cab-narrow">
            <span className="eyebrow">Первый вход</span>
            <h1>Задайте пароль</h1>
            <p className="lead">
              По нему вы будете входить в кабинет
              {email ? (
                <>
                  {" "}
                  под адресом <strong>{email}</strong>
                </>
              ) : null}
              . Забудете — пришлём ссылку для входа на почту.
            </p>

            <div className="cab-card">
              <div className="form-field">
                <label htmlFor="pw1">Пароль</label>
                <input
                  id="pw1"
                  type={showPw ? "text" : "password"}
                  autoComplete="new-password"
                  value={pw1}
                  onChange={(event) => setPw1(event.target.value)}
                />
                <p className={pw1.length >= 8 ? "cab-rule is-ok" : "cab-rule"}>
                  {pw1.length >= 8 ? "Длина подходит" : "Не меньше 8 символов"}
                </p>
              </div>

              <div className="form-field">
                <label htmlFor="pw2">Повторите пароль</label>
                <input
                  id="pw2"
                  type={showPw ? "text" : "password"}
                  autoComplete="new-password"
                  value={pw2}
                  onChange={(event) => setPw2(event.target.value)}
                />
                <p className={pw2 && pw1 === pw2 ? "cab-rule is-ok" : "cab-rule"}>
                  {pw2 && pw1 === pw2 ? "Пароли совпадают" : "Введите пароль ещё раз"}
                </p>
              </div>

              <label className="cab-show">
                <input
                  type="checkbox"
                  checked={showPw}
                  onChange={(event) => setShowPw(event.target.checked)}
                />
                Показать пароль
              </label>

              {setpwError ? (
                <div className="result err show" role="alert">
                  {setpwError}
                </div>
              ) : null}

              <button
                className="btn btn--primary cab-submit"
                type="button"
                onClick={() => void handleSetPassword()}
              >
                Сохранить и войти
              </button>
            </div>
          </section>
        ) : null}

        {/* Кабинет */}
        {view ? (
          <section className="cab-narrow">
            <span className="eyebrow">Личный кабинет</span>
            <h1>{name ? `Здравствуйте, ${name}` : "Здравствуйте"}</h1>
            <p className="lead">
              Здесь виден путь вашего брифа и состояние рекламы. Расход не показываем — только
              результат.
            </p>

            {view.briefs.length > 0 ? (
              view.briefs.map((brief) => (
                <section className="cab-card" key={brief.id} aria-labelledby={`brief-${brief.id}`}>
                  <div className="cab-card__head">
                    <h2 id={`brief-${brief.id}`}>
                      Бриф <span className="cab-mono">№{brief.id}</span>
                    </h2>
                    <span className="badge">{VARIANT_RU[brief.variant] ?? brief.variant}</span>
                  </div>
                  <StatusTrack status={brief.status} />
                </section>
              ))
            ) : (
              <section className="cab-card cab-empty">
                <h2>Брифов пока нет</h2>
                <p>Как только вы заполните бриф, здесь появится его статус и состояние кампании.</p>
                <a className="btn btn--primary" href="/brief-individual.html">
                  Заполнить бриф
                </a>
              </section>
            )}

            {view.referral_url ? <ReferralBlock url={view.referral_url} /> : null}

            <section className="cab-card" aria-labelledby="profile-title">
              <h2 id="profile-title">Ваши контакты</h2>
              {contacts.length > 0 ? (
                <dl className="cab-contacts">
                  {contacts.map((line) => (
                    <div key={line.label}>
                      <dt>{line.label}</dt>
                      <dd>{line.value}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="cab-card__sub">Контакты не указаны.</p>
              )}
            </section>
          </section>
        ) : null}

        {error ? (
          <div className="cab-narrow">
            <div className="result err show" role="alert">
              {error}
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
}
