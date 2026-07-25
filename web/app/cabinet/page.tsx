"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

const STATUS_RU: Record<string, string> = {
  received: "Принят",
  parsed: "В работе",
  prepared: "Готов к запуску",
  launched: "Запущена",
};

const VARIANT_RU: Record<string, string> = {
  individual: "Физлицо",
  community: "Бизнес (ИП/ООО)",
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

export default function CabinetPage() {
  const [view, setView] = useState<CabinetView | null>(null);
  const [needPassword, setNeedPassword] = useState(false);
  const [error, setError] = useState("");
  const [setpwError, setSetpwError] = useState("");
  const [pw1, setPw1] = useState("");
  const [pw2, setPw2] = useState("");

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
    view?.email ? `Email: ${view.email}` : null,
    view?.phone ? `Телефон: ${view.phone}` : null,
    view?.telegram ? `Telegram: ${view.telegram}` : null,
  ].filter((line): line is string => line !== null);

  return (
    <>
      <header className="topbar">
        <div className="brand">
          VK<span>·</span>Ads<span>·</span>auto
        </div>
      </header>

      <main className="wrap">
        <div className="eyebrow">Личный кабинет</div>

        {/* Просмотр кабинета */}
        <section id="view" hidden={!view}>
          <h2 id="greeting">
            {view?.full_name ? `Здравствуйте, ${view.full_name}!` : "Здравствуйте!"}
          </h2>
          <p className="lead">Статус ваших брифов. Расход не показываем — только результат.</p>

          <div className="card" id="profile" style={{ marginBottom: 16 }}>
            <h3>Профиль</h3>
            {contacts.length ? (
              <p>
                {contacts.map((line, index) => (
                  <span key={line}>
                    {index > 0 ? <br /> : null}
                    {line}
                  </span>
                ))}
              </p>
            ) : (
              <p className="note">—</p>
            )}
          </div>

          <div className="section-title">Ваши брифы</div>
          <div id="briefs">
            {view?.briefs?.length ? (
              view.briefs.map((brief) => (
                <div className="card" style={{ marginBottom: 12 }} key={brief.id}>
                  <h3>
                    Бриф №{brief.id} · {VARIANT_RU[brief.variant] ?? brief.variant}
                  </h3>
                  <p>Статус: {STATUS_RU[brief.status] ?? brief.status}</p>
                </div>
              ))
            ) : (
              <p className="note">Брифов пока нет.</p>
            )}
          </div>

          <div
            className="card"
            id="referral"
            style={{ marginTop: 16 }}
            hidden={!view?.referral_url}
          >
            <h3>Пригласите клиента — получите скидку</h3>
            <p className="note">Ваша реферальная ссылка:</p>
            <p>
              <a href={view?.referral_url ?? "#"}>{view?.referral_url}</a>
            </p>
          </div>

          <p style={{ marginTop: 20 }}>
            <a
              href="#"
              id="logout"
              onClick={(event) => {
                event.preventDefault();
                void handleLogout();
              }}
            >
              Выйти
            </a>
          </p>
        </section>

        {/* Первый вход: установка пароля */}
        <section id="setpw" hidden={!needPassword} style={{ maxWidth: 440 }}>
          <h2>Задайте пароль для входа</h2>
          <p className="lead">
            Придумайте пароль — по нему вы будете входить в кабинет.{" "}
            <strong>Запишите или запомните его.</strong> Минимум 8 символов.
          </p>
          <div className="form-field">
            <label htmlFor="pw1">Пароль</label>
            <input
              id="pw1"
              type="password"
              autoComplete="new-password"
              value={pw1}
              onChange={(event) => setPw1(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="pw2">Повторите пароль</label>
            <input
              id="pw2"
              type="password"
              autoComplete="new-password"
              value={pw2}
              onChange={(event) => setPw2(event.target.value)}
            />
          </div>
          <div className="result err" id="setpw-error" hidden={!setpwError}>
            {setpwError}
          </div>
          <button
            id="setpw-btn"
            className="btn btn--primary"
            type="button"
            onClick={() => void handleSetPassword()}
          >
            Сохранить пароль и войти
          </button>
        </section>

        <div className="result err" id="error" hidden={!error}>
          {error}
        </div>
      </main>
    </>
  );
}
