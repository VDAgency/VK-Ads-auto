"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AdminShell } from "@/components/admin/AdminShell";
import { ChangePasswordModal } from "@/components/admin/ChangePasswordModal";
import { LoginScreen } from "@/components/admin/LoginScreen";
import { BriefsScreen } from "@/components/admin/screens/BriefsScreen";
import { CampaignsScreen } from "@/components/admin/screens/CampaignsScreen";
import { ChannelsScreen } from "@/components/admin/screens/ChannelsScreen";
import { ClientsScreen } from "@/components/admin/screens/ClientsScreen";
import { OverviewScreen } from "@/components/admin/screens/OverviewScreen";
import { adminFetch, onSessionExpired, type AdminMe, type Flash } from "@/lib/adminApi";
import { parseRoute, routeHash, type Route } from "@/lib/adminRoute";

import "./admin.css";

type Auth = "checking" | "need" | "ok";

/** Сколько показывать обратимое подтверждение, прежде чем убрать само
 * (spec 2026-08-31 §7: «успех — короткое подтверждение, исчезающее само»). */
const FLASH_AUTO_HIDE_MS = 4000;

export default function AdminPage() {
  const [auth, setAuth] = useState<Auth>("checking");
  const [operatorId, setOperatorId] = useState<number | null>(null);
  // Показывается на экране входа — либо магик-линк не сработал, либо сессия
  // закончилась и оператора вернуло сюда (см. подписку на `onSessionExpired` ниже).
  const [loginNotice, setLoginNotice] = useState("");
  const [route, setRoute] = useState<Route>({ screen: "overview" });
  const [flash, setFlash] = useState<Flash>(null);
  const [showChangePassword, setShowChangePassword] = useState(false);

  const enterApp = useCallback(async () => {
    const me = await adminFetch<AdminMe>("/me");
    setOperatorId(me.operator_id);
    setAuth("ok");
  }, []);

  // Единообразная реакция на «сессия больше не действует» (spec: 401 от любого
  // запроса `/api/v1/admin/*` — не только по сроку, но и если оператора убрали
  // из списка на сервере). Реагируем только из состояния «ok»: на экране входа
  // (пароль не подошёл, магик-линк истёк) это событие тоже долетает через тот
  // же `adminFetch`, но там уже есть своё, более точное сообщение — не перебиваем.
  const authRef = useRef(auth);
  useEffect(() => {
    authRef.current = auth;
  }, [auth]);

  useEffect(() => {
    return onSessionExpired(() => {
      if (authRef.current !== "ok") return;
      setOperatorId(null);
      setAuth("need");
      setLoginNotice("Нужно войти заново — доступ пришлось подтвердить снова.");
    });
  }, []);

  // Магик-линк из бота (`?token=`) продолжает работать как раньше: меняем его
  // на сессию и убираем из адресной строки, чтобы не осталось в истории.
  useEffect(() => {
    async function init() {
      const token = new URLSearchParams(location.search).get("token");

      if (token) {
        try {
          await adminFetch("/authenticate", {
            method: "POST",
            body: JSON.stringify({ token }),
          });
          history.replaceState(null, "", "/admin.html");
          await enterApp();
        } catch {
          setAuth("need");
          setLoginNotice(
            "Ссылка недействительна или истекла. Войдите по паролю или запросите новую в боте: /admin.",
          );
        }
        return;
      }

      try {
        await enterApp();
      } catch {
        setAuth("need");
      }
    }

    void init();
  }, [enterApp]);

  // Раздел читается из hash при загрузке и при «назад»/«вперёд» браузера.
  // Начальное состояние — фиксированный «overview»: `location` недоступен на
  // этапе статического рендера, читать его можно только после монтирования.
  useEffect(() => {
    const applyHash = () => setRoute(parseRoute(location.hash));
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, []);

  // Обратимые подтверждения исчезают сами; необратимые (`persistent`) остаются
  // до следующего действия оператора.
  useEffect(() => {
    if (!flash || !flash.ok || flash.persistent) return;
    const timer = setTimeout(() => setFlash(null), FLASH_AUTO_HIDE_MS);
    return () => clearTimeout(timer);
  }, [flash]);

  function navigate(hash: string) {
    location.hash = hash;
  }

  async function logout() {
    try {
      await adminFetch("/logout", { method: "POST" });
    } catch {
      // Даже если сервер не ответил, уводим оператора со страницы.
    }
    location.href = "/admin.html";
  }

  if (auth !== "ok") {
    return <LoginScreen notice={loginNotice} onLoggedIn={() => void enterApp()} />;
  }

  return (
    <>
      <AdminShell
        active={route.screen}
        operatorId={operatorId as number}
        onChangePassword={() => setShowChangePassword(true)}
        onLogout={() => void logout()}
      >
        {route.screen === "overview" ? <OverviewScreen onNavigate={navigate} /> : null}
        {route.screen === "clients" ? (
          <ClientsScreen
            clientId={route.id}
            onOpenClient={(id) => navigate(routeHash.clients(id))}
            onCloseClient={() => navigate(routeHash.clients())}
            onOpenBrief={(id) => navigate(routeHash.briefs(id))}
          />
        ) : null}
        {route.screen === "briefs" ? (
          <BriefsScreen
            briefId={route.id}
            onOpenBrief={(id) => navigate(routeHash.briefs(id))}
            onCloseBrief={() => navigate(routeHash.briefs())}
            flash={flash}
            onFlash={setFlash}
          />
        ) : null}
        {route.screen === "campaigns" ? <CampaignsScreen /> : null}
        {route.screen === "channels" ? <ChannelsScreen /> : null}
      </AdminShell>

      {showChangePassword ? (
        <ChangePasswordModal onClose={() => setShowChangePassword(false)} />
      ) : null}
    </>
  );
}
