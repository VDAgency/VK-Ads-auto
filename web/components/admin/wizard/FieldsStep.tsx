"use client";

// Шаг 0 мастера — поля брифа и правки (spec 2026-08-31-admin-cabinet-design-brief.md §5).
// Нумерация полей не ломается: по этим номерам оператор диктует боту правки
// `номер.значение`, номер остаётся моноширинным и видимым у каждого поля.

import { useState } from "react";

import { adminFetch, type BriefCard, type Flash } from "@/lib/adminApi";

/** Разбор правок формата `номер.значение`, по одной на строку. */
function parseEdits(text: string): Record<string, string> {
  const edits: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^\s*(\d+)\s*\.\s*(.+?)\s*$/);
    if (match) edits[match[1]] = match[2];
  }
  return edits;
}

export function FieldsStep({
  brief_id,
  card,
  onCardUpdate,
  onFlash,
  onContinue,
}: {
  brief_id: number;
  card: BriefCard;
  onCardUpdate: (card: BriefCard) => void;
  onFlash: (flash: Flash) => void;
  onContinue: () => void;
}) {
  const [showEdits, setShowEdits] = useState(false);
  const [edits, setEdits] = useState("");
  const [applying, setApplying] = useState(false);

  async function applyEdits() {
    const parsed = parseEdits(edits);
    if (!Object.keys(parsed).length) {
      onFlash({ text: "Не понял правки. Формат: номер.значение", ok: false });
      return;
    }
    setApplying(true);
    try {
      const data = await adminFetch<BriefCard>(`/briefs/${brief_id}`, {
        method: "PATCH",
        body: JSON.stringify({ edits: parsed }),
      });
      onCardUpdate(data);
      setEdits("");
      onFlash({
        text:
          "Правки применены." +
          (data.unknown?.length ? ` Неизвестные номера: ${data.unknown.join(", ")}` : ""),
        ok: true,
      });
    } catch {
      onFlash({ text: "Не удалось применить правки.", ok: false });
    } finally {
      setApplying(false);
    }
  }

  return (
    <div>
      <p className="adm-card__contacts">
        {card.client.full_name || "Без имени"}
        {" · "}
        {[card.client.email, card.client.phone, card.client.telegram].filter(Boolean).join(" · ")}
      </p>

      {card.surface_title ? (
        <p className="adm-card__surface">
          Площадка по разбору брифа: <strong>{card.surface_title}</strong>
        </p>
      ) : null}

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

      <div className="adm-actions">
        <button
          className="btn"
          type="button"
          onClick={() => setShowEdits((value) => !value)}
          aria-expanded={showEdits}
        >
          Внести правки
        </button>
        <button className="btn btn--primary" type="button" onClick={onContinue}>
          Продолжить
        </button>
      </div>

      {showEdits ? (
        <div className="adm-panel">
          <p className="adm-panel__hint">
            {"Формат: номер.значение, по одной правке на строку. Например:\n" +
              (card.fields[0] ? `${card.fields[0].n}. новое значение\n` : "") +
              (card.fields[1] ? `${card.fields[1].n}. новое значение` : "")}
          </p>
          <textarea rows={4} value={edits} onChange={(event) => setEdits(event.target.value)} />
          <button
            className="btn btn--primary"
            type="button"
            disabled={applying}
            onClick={() => void applyEdits()}
          >
            {applying ? "Применяем…" : "Применить"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
