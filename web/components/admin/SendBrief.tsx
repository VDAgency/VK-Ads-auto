"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { adminFetch, type InviteResult } from "@/lib/adminApi";

/**
 * Веб-канал отправки брифа клиенту — на случай, когда Telegram недоступен.
 * Сообщение о результате локальное (внутри карточки), как и прежде.
 */
export function SendBrief() {
  const [variant, setVariant] = useState<"individual" | "community">("individual");
  const [contact, setContact] = useState("");
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  async function send() {
    const value = contact.trim();
    if (!value) {
      setMsg({ text: "Введите контакт клиента.", ok: false });
      return;
    }

    let result: InviteResult;
    try {
      result = await adminFetch<InviteResult>("/invites", {
        method: "POST",
        body: JSON.stringify({ variant, contact: value }),
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        setMsg({
          text: "Не распознан контакт. Введите email, @username или телефон.",
          ok: false,
        });
      } else {
        setMsg({ text: "Не удалось отправить. Попробуйте позже.", ok: false });
      }
      return;
    }

    let text: string;
    if (result.status === "sent" && (result.channel === "telegram" || result.channel === "email")) {
      text = `✅ Отправлено через ${result.channel === "telegram" ? "Telegram" : "email"}. Ожидаем бриф.`;
    } else if (result.status === "sent" && result.channel === "manual") {
      text = `📞 Автоотправка на телефон невозможна. Перешлите клиенту вручную:\n\n${result.fallback_text || ""}`;
    } else {
      text = `⚠️ Не удалось отправить автоматически. Перешлите вручную:\n\n${result.fallback_text || ""}`;
    }
    setMsg({ text, ok: result.status === "sent" });
  }

  return (
    <div className="card" style={{ maxWidth: 520 }}>
      <h3>Отправить бриф клиенту</h3>
      <p className="note">
        Бот пришлёт клиенту ссылку на бриф. Веб-канал работает, даже если Telegram недоступен.
      </p>

      <div className="form-field">
        <label>Тип клиента</label>
        <label style={{ fontWeight: "normal", marginRight: 16 }}>
          <input
            type="radio"
            name="sb-variant"
            value="individual"
            checked={variant === "individual"}
            onChange={() => setVariant("individual")}
          />{" "}
          Физлицо
        </label>
        <label style={{ fontWeight: "normal" }}>
          <input
            type="radio"
            name="sb-variant"
            value="community"
            checked={variant === "community"}
            onChange={() => setVariant("community")}
          />{" "}
          Юрлицо / бизнес
        </label>
      </div>

      <div className="form-field">
        <label htmlFor="sb-contact">Контакт клиента (email, @telegram или телефон)</label>
        <input
          id="sb-contact"
          placeholder="client@mail.ru / @username / +7..."
          value={contact}
          onChange={(event) => setContact(event.target.value)}
        />
      </div>

      <button className="btn btn--primary" id="sb-send" type="button" onClick={() => void send()}>
        Отправить
      </button>

      <div
        className={msg ? `result show ${msg.ok ? "ok" : "err"}` : "result"}
        id="sb-msg"
        hidden={!msg}
        style={{ marginTop: 12, whiteSpace: "pre-wrap" }}
      >
        {msg?.text}
      </div>
    </div>
  );
}
