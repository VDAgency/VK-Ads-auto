"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { adminFetch, STATUS_RU, VARIANT_RU, type BriefCard, type Flash } from "@/lib/adminApi";

import { BackLink } from "./Lists";

/** Разбор правок формата `номер.значение`, по одной на строку. */
function parseEdits(text: string): Record<string, string> {
  const edits: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^\s*(\d+)\s*\.\s*(.+?)\s*$/);
    if (match) edits[match[1]] = match[2];
  }
  return edits;
}

/** Файл → base64 без префикса data:. */
function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/** Размеры изображения для валидации на стороне ядра; для видео — нули. */
function imageSize(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    if (!file.type.startsWith("image/")) {
      resolve({ width: 0, height: 0 });
      return;
    }
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve({ width: 0, height: 0 });
    img.src = URL.createObjectURL(file);
  });
}

export function BriefCardView({
  id,
  onBack,
  onFlash,
}: {
  id: number;
  onBack: () => void;
  onFlash: (flash: Flash) => void;
}) {
  const [card, setCard] = useState<BriefCard | null>(null);
  const [showEdits, setShowEdits] = useState(false);
  const [showCreative, setShowCreative] = useState(false);
  const [edits, setEdits] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void adminFetch<BriefCard>(`/briefs/${id}`)
      .then(setCard)
      .catch(() => setCard(null));
  }, [id]);

  async function applyEdits() {
    const parsed = parseEdits(edits);
    if (!Object.keys(parsed).length) {
      onFlash({ text: "Не понял правки. Формат: номер.значение", ok: false });
      return;
    }
    try {
      const data = await adminFetch<BriefCard>(`/briefs/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ edits: parsed }),
      });
      setCard(data);
      onFlash({
        text:
          "Правки применены." +
          (data.unknown?.length ? ` Неизвестные номера: ${data.unknown.join(", ")}` : ""),
        ok: true,
      });
    } catch {
      onFlash({ text: "Не удалось применить правки.", ok: false });
    }
  }

  async function uploadCreative() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      onFlash({ text: "Выберите фото или видео.", ok: false });
      return;
    }

    onFlash({ text: "Загрузка…", ok: true });
    const mediaType = file.type.startsWith("video/") ? "video" : "photo";
    const [b64, size] = await Promise.all([readFileBase64(file), imageSize(file)]);

    try {
      const data = await adminFetch<{ message: string }>(`/briefs/${id}/creative`, {
        method: "POST",
        body: JSON.stringify({
          media_b64: b64,
          media_type: mediaType,
          width: size.width,
          height: size.height,
          title,
          body,
        }),
      });
      onFlash({ text: data.message, ok: true });
      const fresh = await adminFetch<BriefCard>(`/briefs/${id}`);
      setCard(fresh);
    } catch (error) {
      let reason = "Креатив не принят.";
      if (error instanceof ApiError) {
        const detail = error.detail as { issues?: string[]; missing?: string[] } | undefined;
        if (detail?.issues) reason = detail.issues.join(" ");
        else if (detail?.missing) reason = `Бриф неполный: ${detail.missing.join(", ")}`;
      }
      onFlash({ text: reason, ok: false });
    }
  }

  if (!card) return null;

  return (
    <>
      <BackLink label="← к брифам" onClick={onBack} />

      <div className="adm-card">
        <div className="adm-card__head">
          <h2>
            Бриф <span className="adm-mono">№{card.brief_id}</span>
          </h2>
          <span className="adm-badge">{VARIANT_RU[card.variant] ?? card.variant}</span>
          <span
            className={card.status === "launched" ? "adm-badge adm-badge--accent" : "adm-badge"}
          >
            {STATUS_RU[card.status] ?? card.status}
          </span>
          <span
            className={
              card.has_creative ? "adm-badge adm-badge--accent" : "adm-badge--wait adm-badge"
            }
          >
            {card.has_creative ? "креатив загружен" : "креатива нет"}
          </span>
        </div>
        <p className="adm-card__contacts">
          {card.client.full_name || "Без имени"}
          {" · "}
          {[card.client.email, card.client.phone, card.client.telegram].filter(Boolean).join(" · ")}
        </p>

        {/* Таблица, а не сплошной список: номер — рабочий инструмент, по нему
            идут правки `номер.значение`, и он обязан находиться взглядом. */}
        <dl className="adm-fields">
          {card.fields.map((field) => (
            <div className="adm-field" key={field.n}>
              <dt className="adm-field__n">{field.n}</dt>
              <dd className="adm-field__label">{field.label}</dd>
              <dd className={field.value ? "adm-field__value" : "adm-field__value is-empty"}>
                {field.value || "не заполнено"}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="adm-actions">
        <button
          className="btn"
          id="edit-toggle"
          type="button"
          onClick={() => setShowEdits((value) => !value)}
        >
          Внести правки
        </button>
        <button
          className="btn btn--primary"
          id="creative-toggle"
          type="button"
          onClick={() => setShowCreative((value) => !value)}
        >
          Загрузить креатив
        </button>
      </div>

      <div className="adm-panel" id="edit-box" hidden={!showEdits}>
        <p className="adm-panel__hint">
          {"Формат: номер.значение, по одной правке на строку. Например:\n" +
            (card.fields[0] ? `${card.fields[0].n}. новое значение\n` : "") +
            (card.fields[1] ? `${card.fields[1].n}. новое значение` : "")}
        </p>
        <textarea
          id="edits"
          rows={4}
          value={edits}
          onChange={(event) => setEdits(event.target.value)}
        />
        <button
          className="btn btn--primary"
          id="edit-send"
          type="button"
          onClick={() => void applyEdits()}
        >
          Применить
        </button>
      </div>

      <div className="adm-panel" id="creative-box" hidden={!showCreative}>
        <div className="form-field">
          <label htmlFor="cr-file">Фото или видео</label>
          <input type="file" id="cr-file" accept="image/*,video/*" ref={fileRef} />
        </div>
        <div className="form-field">
          <label htmlFor="cr-title">Заголовок</label>
          <input
            id="cr-title"
            type="text"
            maxLength={40}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <p className="adm-panel__hint">{title.length} из 40</p>
        </div>
        <div className="form-field">
          <label htmlFor="cr-body">Текст</label>
          <textarea
            id="cr-body"
            rows={3}
            maxLength={220}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
          <p className="adm-panel__hint">{body.length} из 220</p>
        </div>
        {/* Единственное необратимое действие в панели — отделено и названо прямо. */}
        <button
          className="btn btn--primary"
          id="cr-send"
          type="button"
          onClick={() => void uploadCreative()}
        >
          Отправить и запустить кампанию
        </button>
      </div>
    </>
  );
}
