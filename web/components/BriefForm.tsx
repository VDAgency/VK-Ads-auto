"use client";

import { useRef, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

/** Вариант брифа: определяет и набор полей, и то, что уходит в ядро. */
export type BriefVariant = "individual" | "community";

type SelectOption = { value: string; label: string };

/**
 * Строка формы. Порядок элементов массива — это и порядок на экране, и порядок
 * нумерации, поэтому менять его в отрыве от `services/brief_fields.py` нельзя:
 * по этим номерам оператор правит сводку в боте (`номер.значение`).
 */
export type BriefRow =
  | { kind: "section"; title: string }
  | {
      kind: "input" | "textarea";
      num: string;
      name: string;
      label: string;
      error?: string;
      type?: string;
      placeholder?: string;
      autoComplete?: string;
      inputMode?: "numeric" | "tel";
      required?: boolean;
      hint?: { href: string; text: string };
    }
  | {
      kind: "select";
      num: string;
      name: string;
      label: string;
      error?: string;
      options: SelectOption[];
    }
  // Отдельная строка «Возраст (от / до)»: один номер, два инпута.
  | { kind: "age"; num: string; label: string };

type BriefFormProps = {
  variant: BriefVariant;
  rows: BriefRow[];
};

type ResultKind = "ok" | "err";

/**
 * Форма брифа. Перенос логики прежнего `web/static/app.js` один в один:
 * сбор всех `[name]`, проброс `?t=` (токен инвайта) и `?ref=` (рефералка),
 * подсветка незаполненных полей по ответу 422, редирект в кабинет по
 * `cabinet_url` из ответа 201.
 */
export function BriefForm({ variant, rows }: BriefFormProps) {
  const [invalid, setInvalid] = useState<ReadonlySet<string>>(new Set());
  const [result, setResult] = useState<{ kind: ResultKind; message: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  function showResult(kind: ResultKind, message: string) {
    setResult({ kind, message });
    // Прежнее поведение: подвести пользователя к сообщению о результате.
    requestAnimationFrame(() => {
      resultRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;

    const payload: Record<string, string> = {};
    for (const el of form.querySelectorAll<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >("[name]")) {
      payload[el.name] = (el.value || "").trim();
    }

    setInvalid(new Set());
    setSubmitting(true);

    // Токен инвайта (?t=) — по нему ядро находит приглашение, метит его
    // «received» и уведомляет оператора. ?ref= — реферальная ссылка (Блок 2).
    const params = new URLSearchParams(location.search);

    try {
      const data = await apiFetch<{ cabinet_url?: string }>("/briefs", {
        method: "POST",
        body: JSON.stringify({
          variant,
          payload,
          ref_code: params.get("ref"),
          token: params.get("t"),
        }),
      });

      form.reset();

      // Авто-переброс в личный кабинет по magic-link из ответа. Ссылки нет —
      // показываем подтверждение (обратная совместимость).
      if (data?.cabinet_url) {
        showResult("ok", "Бриф отправлен. Открываем ваш личный кабинет…");
        location.href = data.cabinet_url;
      } else {
        showResult("ok", "Бриф отправлен. Спасибо — мы свяжемся с вами.");
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        const detail = error.detail as { missing?: string[] } | undefined;
        setInvalid(new Set(detail?.missing ?? []));
        showResult("err", "Заполните обязательные поля, отмеченные красным.");
      } else if (error instanceof ApiError) {
        showResult("err", "Не удалось отправить. Попробуйте позже.");
      } else {
        showResult("err", "Нет связи с сервером. Проверьте интернет и попробуйте снова.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <form data-variant={variant} noValidate onSubmit={handleSubmit}>
        {rows.map((row, index) => {
          if (row.kind === "section") {
            return (
              <div className="section-title" key={`section-${index}`}>
                {row.title}
              </div>
            );
          }

          if (row.kind === "age") {
            return (
              <div className="field" key={row.num}>
                <div className="num">{row.num}</div>
                <div>
                  <label>{row.label}</label>
                  <div style={{ display: "flex", gap: 12 }}>
                    <input name="age_from" inputMode="numeric" placeholder="от" />
                    <input name="age_to" inputMode="numeric" placeholder="до" />
                  </div>
                </div>
              </div>
            );
          }

          const isInvalid = invalid.has(row.name);

          return (
            <div className={isInvalid ? "field invalid" : "field"} key={row.num}>
              <div className="num">{row.num}</div>
              <div>
                <label htmlFor={row.name}>{row.label}</label>

                {row.kind === "input" && (
                  <input
                    id={row.name}
                    name={row.name}
                    type={row.type}
                    placeholder={row.placeholder}
                    autoComplete={row.autoComplete}
                    inputMode={row.inputMode}
                    required={row.required}
                  />
                )}

                {row.kind === "textarea" && <textarea id={row.name} name={row.name}></textarea>}

                {row.kind === "select" && (
                  <select id={row.name} name={row.name} defaultValue={row.options[0]?.value ?? ""}>
                    {row.options.map((option) => (
                      <option value={option.value} key={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                )}

                {row.error ? <div className="error">{row.error}</div> : null}

                {row.kind !== "select" && row.hint ? (
                  <a className="hint" href={row.hint.href} target="_blank" rel="noopener">
                    {row.hint.text}
                  </a>
                ) : null}
              </div>
            </div>
          );
        })}

        <button type="submit" className="btn btn--primary" disabled={submitting}>
          Отправить бриф
        </button>
      </form>

      <div
        ref={resultRef}
        className={result ? `result show ${result.kind}` : "result"}
        role="status"
        aria-live="polite"
      >
        {result?.message}
      </div>
    </>
  );
}
