"use client";

import { useEffect, useRef } from "react";

import type { Screen } from "@/lib/adminRoute";

const NAV_ITEMS: { screen: Screen; label: string; hash: string }[] = [
  { screen: "overview", label: "Обзор", hash: "#/overview" },
  { screen: "clients", label: "Клиенты", hash: "#/clients" },
  { screen: "briefs", label: "Брифы", hash: "#/briefs" },
  { screen: "campaigns", label: "Кампании", hash: "#/campaigns" },
  { screen: "channels", label: "Кабинеты и каналы", hash: "#/channels" },
];

const SECTION_LABEL: Record<Screen, string> = {
  overview: "Обзор",
  clients: "Клиенты",
  briefs: "Брифы",
  campaigns: "Кампании",
  channels: "Кабинеты и каналы",
};

/**
 * Оболочка кабинета: шапка + постоянная левая навигация + рабочая область.
 *
 * Смена раздела объявляется скринридеру двумя способами разом (spec 2026-08-31
 * §8): фокус переходит на заголовок раздела (обычные скринридеры читают то, на
 * что переведён фокус) и дублируется тихой строкой `aria-live="polite"` — так
 * объявление доходит и там, где перевод фокуса на нередактируемый элемент не
 * подхватывается.
 */
export function AdminShell({
  active,
  operatorId,
  onChangePassword,
  onLogoutAll,
  onLogout,
  children,
}: {
  active: Screen;
  operatorId: number;
  onChangePassword: () => void;
  onLogoutAll: () => void;
  onLogout: () => void;
  children: React.ReactNode;
}) {
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    titleRef.current?.focus();
  }, [active]);

  return (
    <div className="adm-shell">
      <a className="skip-link" href="#adm-content">
        Перейти к содержимому
      </a>

      <header className="adm-header">
        <div className="adm-header__inner">
          <span className="adm-brand">
            <span className="adm-brand__name">
              Ads<span className="adm-brand__dot">·</span>auto
            </span>
            <span className="adm-brand__role">панель оператора</span>
          </span>
          <div className="adm-header__actions">
            <span className="adm-operator">
              Telegram ID <span className="adm-mono">{operatorId}</span>
            </span>
            <button className="btn btn--ghost" type="button" onClick={onChangePassword}>
              Сменить пароль
            </button>
            <button className="btn btn--ghost" type="button" onClick={onLogoutAll}>
              Выйти на всех устройствах
            </button>
            <button className="btn btn--ghost" type="button" onClick={onLogout}>
              Выйти
            </button>
          </div>
        </div>
      </header>

      <div className="adm-body">
        <nav className="adm-sidenav" aria-label="Разделы кабинета">
          <ul>
            {NAV_ITEMS.map((item) => (
              <li key={item.screen}>
                <a
                  href={item.hash}
                  className={
                    active === item.screen ? "adm-sidenav__link is-active" : "adm-sidenav__link"
                  }
                  aria-current={active === item.screen ? "page" : undefined}
                >
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <main className="adm-main" id="adm-content">
          <span className="visually-hidden" aria-live="polite">
            {SECTION_LABEL[active]}
          </span>
          <h1 className="adm-page-title" tabIndex={-1} ref={titleRef}>
            {SECTION_LABEL[active]}
          </h1>
          {children}
        </main>
      </div>
    </div>
  );
}
