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

      <div className="card">
        <h3>
          Бриф №{card.brief_id} · {VARIANT_RU[card.variant] ?? card.variant} ·{" "}
          {STATUS_RU[card.status] ?? card.status}
        </h3>
        <p>
          👤 {card.client.full_name || "—"}
          <br />
          {[card.client.email, card.client.phone, card.client.telegram].filter(Boolean).join(" · ")}
        </p>
        <div className="section-title">Поля</div>
        {card.fields.map((field) => (
          <div key={field.n}>
            {field.n}. {field.label}: {field.value || "—"}
          </div>
        ))}
        {card.surface_title ? (
          // Клиент выбирает площадку словами; показываем, как её понял разбор брифа,
          // чтобы ошибка в поле была видна до запуска, а не после.
          <p>🎯 Площадка: {card.surface_title}</p>
        ) : null}
        <p>🖼 Креатив: {card.has_creative ? "загружен" : "не загружен"}</p>
      </div>

      <div className="adm-actions">
        <button
          className="btn"
          id="edit-toggle"
          type="button"
          onClick={() => setShowEdits((value) => !value)}
        >
          ✏️ Внести правки
        </button>
        <button
          className="btn btn--primary"
          id="creative-toggle"
          type="button"
          onClick={() => setShowCreative((value) => !value)}
        >
          🖼 Загрузить креатив
        </button>
      </div>

      <div id="edit-box" hidden={!showEdits}>
        <p className="note">
          Формат: номер.значение, каждая с новой строки. Например:
          <br />
          1. Иван Петров
          <br />
          7. Москва
        </p>
        <textarea
          id="edits"
          rows={4}
          style={{ width: "100%" }}
          value={edits}
          onChange={(event) => setEdits(event.target.value)}
        />
        <button
          className="btn btn--primary"
          id="edit-send"
          type="button"
          style={{ marginTop: 8 }}
          onClick={() => void applyEdits()}
        >
          Применить
        </button>
      </div>

      <div id="creative-box" hidden={!showCreative} style={{ marginTop: 12 }}>
        <div className="field">
          <div>
            <label htmlFor="cr-file">Фото или видео</label>
            <input type="file" id="cr-file" accept="image/*,video/*" ref={fileRef} />
          </div>
        </div>
        <div className="field">
          <div>
            <label htmlFor="cr-title">Заголовок (до 40)</label>
            <input id="cr-title" value={title} onChange={(event) => setTitle(event.target.value)} />
          </div>
        </div>
        <div className="field">
          <div>
            <label htmlFor="cr-body">Текст (до 220)</label>
            <textarea
              id="cr-body"
              rows={3}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </div>
        </div>
        <button
          className="btn btn--primary"
          id="cr-send"
          type="button"
          onClick={() => void uploadCreative()}
        >
          Отправить (запустит РК)
        </button>
      </div>
    </>
  );
}
