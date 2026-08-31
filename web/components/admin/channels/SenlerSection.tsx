"use client";

// Senler: привязка токена сообщества — веб-зеркало `/senler_token` и
// `/senler_unlink` бота (`bot/handlers/senler.py`). Токен — НЕ ключ API Senler
// (проект его сознательно не подключает), а токен доступа к самому сообществу
// VK, которым проверяется штатный `groups.getCallbackServers`: подключён ли к
// сообществу чат-бот Senler. Оператор вводит только сам токен — сообщество
// опознаёт себя сам (`groups.getById` без `group_id`), так что привязка — один
// шаг, без риска перепутать сообщество.

import { useState } from "react";

import {
  adminFetch,
  communityTokenErrorMessage,
  communityTokenNotFound,
  type CommunityTokenOut,
} from "@/lib/adminApi";

type Msg = { text: string; ok: boolean } | null;

export function SenlerSection() {
  const [token, setToken] = useState("");
  const [reference, setReference] = useState("");
  const [busyAttach, setBusyAttach] = useState(false);
  const [busyDetach, setBusyDetach] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function attach() {
    const value = token.trim();
    if (!value) {
      setMsg({ text: "Вставьте токен доступа сообщества.", ok: false });
      return;
    }
    setBusyAttach(true);
    try {
      const result = await adminFetch<CommunityTokenOut>("/senler/community-token", {
        method: "POST",
        body: JSON.stringify({ token: value }),
      });
      // Токен не держим в состоянии дольше отправки.
      setToken("");
      if (result.connected) {
        setMsg({
          text: `Токен сообщества «${result.community_name}» сохранён. Чат-бот Senler подключён — можно запускать кампанию с этой целью.`,
          ok: true,
        });
      } else {
        setMsg({
          text: `Токен сообщества «${result.community_name}» сохранён, но подключение Senler не подтвердилось: ${result.reason}`,
          ok: false,
        });
      }
    } catch (error) {
      setMsg({ text: communityTokenErrorMessage(error), ok: false });
    } finally {
      setBusyAttach(false);
    }
  }

  async function detach() {
    const value = reference.trim();
    if (!value) {
      setMsg({
        text: "Укажите короткий адрес сообщества (после vk.ru/) или его числовой id.",
        ok: false,
      });
      return;
    }
    setBusyDetach(true);
    try {
      await adminFetch(`/senler/community-token?reference=${encodeURIComponent(value)}`, {
        method: "DELETE",
      });
      setReference("");
      setMsg({ text: `Привязка токена для «${value}» снята.`, ok: true });
    } catch (error) {
      setMsg({
        text: communityTokenNotFound(error)
          ? "Активной привязки для этого сообщества не было."
          : "Не удалось снять привязку. Попробуйте ещё раз.",
        ok: false,
      });
    } finally {
      setBusyDetach(false);
    }
  }

  return (
    <div className="adm-card">
      <p className="note">
        Токен выпускает администратор сообщества в его настройках (это не ключ API Senler — он не
        нужен). Значение сохраняется на сервере в зашифрованном виде и обратно не показывается —
        вводите его как пароль.
      </p>

      <div className="form-field">
        <label htmlFor="senler-token">Токен доступа сообщества</label>
        <input
          id="senler-token"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Вставьте токен"
        />
      </div>
      <button
        className="btn btn--primary"
        type="button"
        disabled={busyAttach}
        onClick={() => void attach()}
      >
        {busyAttach ? "Проверяем…" : "Привязать"}
      </button>

      <div className="form-field" style={{ marginTop: "1.5rem" }}>
        <label htmlFor="senler-reference">Отвязать привязку (адрес или id сообщества)</label>
        <input
          id="senler-reference"
          type="text"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          placeholder="club12345 или 12345"
        />
      </div>
      <button className="btn" type="button" disabled={busyDetach} onClick={() => void detach()}>
        {busyDetach ? "Отвязываем…" : "Отвязать"}
      </button>

      {msg ? (
        <div className={`result show ${msg.ok ? "ok" : "err"}`} role="status">
          {msg.text}
        </div>
      ) : null}
    </div>
  );
}
