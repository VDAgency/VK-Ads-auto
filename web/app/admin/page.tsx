"use client";

import { useCallback, useEffect, useState } from "react";

import { BriefCardView } from "@/components/admin/BriefCardView";
import { BriefList, CampaignList, ClientDetailView, ClientList } from "@/components/admin/Lists";
import { SendBrief } from "@/components/admin/SendBrief";
import { adminFetch, type Flash, type Overview } from "@/lib/adminApi";

import "./admin.css";

/** Что показано в основной области. Прежний routeView, только типизированный. */
type Screen =
  | { kind: "send" }
  | { kind: "clients" }
  | { kind: "briefs"; status: "recent" | "pending" }
  | { kind: "campaigns" }
  | { kind: "client"; id: number }
  | { kind: "brief"; id: number };

type Auth = "checking" | "need" | "ok";

export default function AdminPage() {
  const [auth, setAuth] = useState<Auth>("checking");
  const [authError, setAuthError] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [screen, setScreen] = useState<Screen>({ kind: "clients" });
  const [flash, setFlash] = useState<Flash>(null);

  const enterApp = useCallback(async () => {
    setAuth("ok");
    try {
      setOverview(await adminFetch<Overview>("/overview"));
    } catch {
      // Плитки — не критично: без них панель остаётся работоспособной.
    }
  }, []);

  useEffect(() => {
    async function init() {
      const token = new URLSearchParams(location.search).get("token");

      if (token) {
        try {
          await adminFetch("/authenticate", {
            method: "POST",
            body: JSON.stringify({ token }),
          });
          // Убираем токен из URL и входим.
          history.replaceState(null, "", "/admin.html");
          await enterApp();
        } catch {
          setAuth("need");
          setAuthError("Ссылка недействительна или истекла. Запросите новую в боте: /admin.");
        }
        return;
      }

      try {
        await adminFetch("/me");
        await enterApp();
      } catch {
        setAuth("need");
      }
    }

    void init();
  }, [enterApp]);

  function go(next: Screen) {
    setFlash(null);
    setScreen(next);
  }

  async function logout() {
    try {
      await adminFetch("/logout", { method: "POST" });
    } catch {
      // Даже если сервер не ответил, уводим оператора со страницы.
    }
    location.href = "/admin.html";
  }

  /** Активен ли раздел — чтобы оператор видел, где находится. */
  function isActive(kind: Screen["kind"], status?: "recent" | "pending") {
    if (screen.kind !== kind) return false;
    if (kind === "briefs" && screen.kind === "briefs") return screen.status === status;
    return true;
  }

  return (
    <div className="adm-shell">
      <header className="adm-header">
        <div className="adm-header__inner">
          <span className="adm-brand">
            Ads<span className="adm-brand__dot">·</span>auto
            <span className="adm-brand__role">панель оператора</span>
          </span>
          {auth === "ok" ? (
            <button
              className="btn btn--ghost adm-logout"
              type="button"
              onClick={() => void logout()}
            >
              Выйти
            </button>
          ) : null}
        </div>
      </header>

      <main className="adm-main">
        {/* Нужен вход */}
        <section className="adm-gate" hidden={auth !== "need"}>
          <div className="eyebrow">Админ-панель</div>
          <h2>Вход только из бота</h2>
          <p className="lead">
            Откройте админку из Telegram-бота командой <strong>/admin</strong> — бот пришлёт ссылку
            для входа. Она действует 15 минут.
          </p>
          <div className="result err" id="auth-error" hidden={!authError}>
            {authError}
          </div>
        </section>

        {/* Приложение */}
        <section id="app" hidden={auth !== "ok"}>
          <div className="adm-tiles" id="tiles">
            {overview ? (
              <>
                <button
                  className={`adm-tile${isActive("clients") ? " is-active" : ""}`}
                  type="button"
                  onClick={() => go({ kind: "clients" })}
                >
                  <b>{overview.clients}</b>
                  <span>Клиенты</span>
                </button>
                <button
                  className={`adm-tile${overview.pending > 0 ? " is-alert" : ""}${
                    isActive("briefs", "pending") ? " is-active" : ""
                  }`}
                  type="button"
                  onClick={() => go({ kind: "briefs", status: "pending" })}
                >
                  <b>{overview.pending}</b>
                  <span>Ждём брифы</span>
                </button>
                <button
                  className={`adm-tile${isActive("briefs", "recent") ? " is-active" : ""}`}
                  type="button"
                  onClick={() => go({ kind: "briefs", status: "recent" })}
                >
                  <b>{overview.recent}</b>
                  <span>Пришли за неделю</span>
                </button>
                <button
                  className={`adm-tile${isActive("campaigns") ? " is-active" : ""}`}
                  type="button"
                  onClick={() => go({ kind: "campaigns" })}
                >
                  <b>{overview.campaigns}</b>
                  <span>Кампании</span>
                </button>
              </>
            ) : null}
          </div>

          <div className="adm-nav">
            <button
              className={`adm-nav__btn${isActive("send") ? " is-active" : ""}`}
              type="button"
              onClick={() => go({ kind: "send" })}
            >
              Отправить бриф
            </button>
            <button
              className={`adm-nav__btn${isActive("clients") ? " is-active" : ""}`}
              type="button"
              onClick={() => go({ kind: "clients" })}
            >
              Клиенты
            </button>
            <button
              className={`adm-nav__btn${isActive("briefs", "recent") ? " is-active" : ""}`}
              type="button"
              onClick={() => go({ kind: "briefs", status: "recent" })}
            >
              Пришли брифы
            </button>
            <button
              className={`adm-nav__btn${isActive("briefs", "pending") ? " is-active" : ""}`}
              type="button"
              onClick={() => go({ kind: "briefs", status: "pending" })}
            >
              Ждём брифы
            </button>
            <button
              className={`adm-nav__btn${isActive("campaigns") ? " is-active" : ""}`}
              type="button"
              onClick={() => go({ kind: "campaigns" })}
            >
              Кампании
            </button>
          </div>

          <div id="list">
            {auth === "ok" && screen.kind === "send" ? <SendBrief /> : null}
            {auth === "ok" && screen.kind === "clients" ? (
              <ClientList onOpenClient={(id) => go({ kind: "client", id })} />
            ) : null}
            {auth === "ok" && screen.kind === "briefs" ? (
              <BriefList
                key={screen.status}
                status={screen.status}
                onOpenBrief={(id) => go({ kind: "brief", id })}
              />
            ) : null}
            {auth === "ok" && screen.kind === "campaigns" ? <CampaignList /> : null}
          </div>

          <div id="detail">
            {auth === "ok" && screen.kind === "client" ? (
              <ClientDetailView
                id={screen.id}
                onBack={() => go({ kind: "clients" })}
                onOpenBrief={(id) => go({ kind: "brief", id })}
              />
            ) : null}
            {auth === "ok" && screen.kind === "brief" ? (
              <BriefCardView
                id={screen.id}
                onBack={() => go({ kind: "briefs", status: "recent" })}
                onFlash={setFlash}
              />
            ) : null}
          </div>

          <div
            className={flash ? `result show ${flash.ok ? "ok" : "err"}` : "result"}
            id="msg"
            hidden={!flash}
          >
            {flash?.text}
          </div>
        </section>
      </main>
    </div>
  );
}
