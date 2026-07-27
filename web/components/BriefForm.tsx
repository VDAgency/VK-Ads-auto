"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, apiFetch } from "@/lib/api";

/** Вариант брифа: определяет и набор полей, и то, что уходит в ядро. */
export type BriefVariant = "individual" | "community";

/** Вариант выбора в карточках или выпадающем списке. */
export type BriefChoice = {
  /** Значение, уходящее в payload. Разбирается `services/brief_parser.py`. */
  value: string;
  label: string;
  /** Недоступный вариант: рендерится заблокированным с пометкой «скоро». */
  disabled?: boolean;
};

type FieldBase = {
  /** Ключ payload; должен совпадать с ключом в `services/brief_fields.py`. */
  name: string;
  label: string;
  hint?: string;
  required?: boolean;
  /** Текст под полем, когда оно осталось незаполненным. */
  error?: string;
  /** Ограничение длины: без него в поле уходит текст любого размера. */
  maxLength?: number;
  /** Допустимый вид значения (атрибут pattern). */
  pattern?: string;
};

export type BriefField =
  | (FieldBase & {
      kind: "input";
      type?: "text" | "email" | "tel" | "url";
      placeholder?: string;
      autoComplete?: string;
      inputMode?: "numeric" | "tel";
      link?: { href: string; text: string };
    })
  | (FieldBase & { kind: "textarea"; placeholder?: string; rows?: number })
  | (FieldBase & { kind: "select"; placeholder?: string; options: BriefChoice[] })
  | (FieldBase & { kind: "choices"; options: BriefChoice[] })
  // Возраст — одна строка формы, но два поля payload: age_from и age_to.
  | { kind: "age"; label: string; hint?: string };

export type BriefRow =
  | { kind: "section"; num: number; title: string }
  // Пара полей в одну строку на десктопе, друг под другом на мобильном.
  | { kind: "pair"; items: [BriefField, BriefField] }
  | { kind: "instruction"; title: string; steps: ReactNode[] }
  | { kind: "notice"; text: string }
  | BriefField;

type BriefFormProps = {
  variant: BriefVariant;
  rows: BriefRow[];
  /** Подпись под кнопкой отправки. */
  footer?: ReactNode;
};

type ResultKind = "ok" | "err";

/** Ключи payload, которые создаёт строка формы. */
function fieldNames(row: BriefRow): string[] {
  if (row.kind === "section" || row.kind === "instruction" || row.kind === "notice") return [];
  if (row.kind === "pair") return row.items.flatMap(fieldNames);
  if (row.kind === "age") return ["age_from", "age_to"];
  return [row.name];
}

/** Обязательные поля строки, оставшиеся пустыми. */
function missingNames(row: BriefRow, payload: Record<string, string>): string[] {
  if (row.kind === "section" || row.kind === "instruction" || row.kind === "notice") return [];
  if (row.kind === "pair") return row.items.flatMap((item) => missingNames(item, payload));
  if (row.kind === "age" || !row.required) return [];
  return payload[row.name] ? [] : [row.name];
}

/** Все незаполненные обязательные поля — в порядке отображения. */
function collectMissing(rows: BriefRow[], payload: Record<string, string>): string[] {
  return rows.flatMap((row) => missingNames(row, payload));
}

/** Ключ черновика: у каждого варианта брифа свой. */
function draftKey(variant: BriefVariant): string {
  return `vk-ads-auto:brief-draft:${variant}`;
}

/** Прочитать черновик. Любая ошибка хранилища означает «черновика нет». */
function readDraft(variant: BriefVariant): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(draftKey(variant));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as Record<string, string>;
  } catch {
    return {};
  }
}

/**
 * Форма брифа.
 *
 * Разметка следует макетам заказчика (docs/references/*.html): нумерованные
 * секции, подсказка под лейблом, карточки выбора. Логика отправки перенесена из
 * прежнего `web/static/app.js`: проброс `?t=` (токен инвайта) и `?ref=`
 * (рефералка), подсветка полей по ответу 422, редирект в кабинет по
 * `cabinet_url` из ответа 201.
 */
export function BriefForm({ variant, rows, footer }: BriefFormProps) {
  const [invalid, setInvalid] = useState<ReadonlySet<string>>(new Set());
  const [result, setResult] = useState<{ kind: ResultKind; message: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  function showResult(kind: ResultKind, message: string, scroll = true) {
    setResult({ kind, message });
    if (!scroll) return;
    requestAnimationFrame(() => {
      resultRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  /**
   * Подвести к первому незаполненному полю.
   *
   * Без этого человек, заполнивший 19–29 полей, получал общее сообщение внизу
   * страницы и искал красные рамки прокруткой — на телефоне это около
   * четырёх тысяч пикселей вслепую.
   */
  function focusFirstInvalid(name: string) {
    const el = formRef.current?.querySelector<HTMLElement>(`[name="${name}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    // Фокус после прокрутки: иначе браузер сам дёрнет вид на своё место.
    window.setTimeout(() => el.focus({ preventScroll: true }), 320);
  }

  /** Сохранить заполненное. Вызывается на каждый ввод и выбор. */
  function saveDraft() {
    const form = formRef.current;
    if (!form) return;
    const data: Record<string, string> = {};
    for (const [name, value] of new FormData(form).entries()) {
      if (typeof value === "string" && value !== "") data[name] = value;
    }
    try {
      window.localStorage.setItem(draftKey(variant), JSON.stringify(data));
    } catch {
      // Приватный режим или переполненное хранилище: черновик не критичен.
    }
  }

  function clearDraft() {
    try {
      window.localStorage.removeItem(draftKey(variant));
    } catch {
      // см. saveDraft
    }
  }

  // Восстановление черновика. Обновление страницы, кнопка «назад» или звонок
  // на телефоне прежде стирали все ответы без предупреждения.
  useEffect(() => {
    const draft = readDraft(variant);
    const form = formRef.current;
    if (!form || Object.keys(draft).length === 0) return;

    for (const [name, value] of Object.entries(draft)) {
      const el = form.elements.namedItem(name);
      if (el instanceof RadioNodeList) {
        for (const node of Array.from(el)) {
          if (node instanceof HTMLInputElement && node.value === value) node.checked = true;
        }
      } else if (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement
      ) {
        el.value = value;
      }
    }
    showResult("ok", "Мы восстановили то, что вы заполняли раньше.", false);
  }, [variant]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;

    // Payload собирается через FormData, а не обходом всех `[name]`: только так
    // из группы radio попадает ВЫБРАННЫЙ вариант, а заблокированные варианты
    // (недоступные цели) не попадают вовсе. Ключи предварительно заполняются
    // пустыми строками, чтобы форма всегда слала стабильный набор полей.
    const payload: Record<string, string> = {};
    for (const row of rows) {
      for (const name of fieldNames(row)) payload[name] = "";
    }
    for (const [name, value] of new FormData(form).entries()) {
      if (typeof value === "string") payload[name] = value.trim();
    }

    // Проверяем на клиенте, а не ждём ответа 422: незаполненное поле должно
    // находиться до сетевого запроса, а не после него.
    const missing = collectMissing(rows, payload);
    if (missing.length > 0) {
      setInvalid(new Set(missing));
      focusFirstInvalid(missing[0]);
      showResult(
        "err",
        missing.length === 1
          ? "Одно обязательное поле не заполнено — мы к нему перешли."
          : `Не заполнено обязательных полей: ${missing.length}. Мы перешли к первому.`,
        false,
      );
      return;
    }

    setInvalid(new Set());
    setSubmitting(true);

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
      clearDraft();

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
        const serverMissing = detail?.missing ?? [];
        setInvalid(new Set(serverMissing));
        if (serverMissing.length > 0) {
          focusFirstInvalid(serverMissing[0]);
          showResult("err", "Заполните обязательные поля, отмеченные красным.", false);
        } else {
          showResult("err", "Заполните обязательные поля, отмеченные красным.");
        }
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
      <form
        ref={formRef}
        data-variant={variant}
        noValidate
        onSubmit={handleSubmit}
        onInput={saveDraft}
        onChange={saveDraft}
      >
        {rows.map((row, index) => (
          <Row key={rowKey(row, index)} row={row} invalid={invalid} />
        ))}

        <div className="bf-submit">
          <button type="submit" className="bf-btn" disabled={submitting}>
            {submitting ? "Отправляем…" : "Отправить бриф"}
          </button>
          {footer ? <div className="bf-foot">{footer}</div> : null}
        </div>
      </form>

      <div
        ref={resultRef}
        className={result ? `bf-result is-shown is-${result.kind}` : "bf-result"}
        role="status"
        aria-live="polite"
      >
        {result?.message}
      </div>
    </>
  );
}

function rowKey(row: BriefRow, index: number): string {
  if (row.kind === "section") return `section-${row.num}`;
  if (row.kind === "instruction" || row.kind === "notice" || row.kind === "pair") {
    return `${row.kind}-${index}`;
  }
  if (row.kind === "age") return "age";
  return row.name;
}

function Row({ row, invalid }: { row: BriefRow; invalid: ReadonlySet<string> }) {
  if (row.kind === "section") {
    return (
      <div className="bf-section__head">
        <div className="bf-section__num" aria-hidden="true">
          {row.num}
        </div>
        <h2 className="bf-section__title">{row.title}</h2>
      </div>
    );
  }

  if (row.kind === "instruction") {
    return (
      <div className="bf-instruction">
        <div className="bf-instruction__title">{row.title}</div>
        <ol>
          {row.steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
      </div>
    );
  }

  if (row.kind === "notice") {
    return (
      <p className="bf-notice">
        <span aria-hidden="true">⚠️</span>
        <span>{row.text}</span>
      </p>
    );
  }

  if (row.kind === "pair") {
    return (
      <div className="bf-two">
        {row.items.map((item) => (
          <Field key={fieldNames(item).join("-")} field={item} invalid={invalid} />
        ))}
      </div>
    );
  }

  return <Field field={row} invalid={invalid} />;
}

function Field({ field, invalid }: { field: BriefField; invalid: ReadonlySet<string> }) {
  if (field.kind === "age") {
    return (
      <div className="bf-field">
        <span className="bf-field__label">{field.label}</span>
        {field.hint ? <span className="bf-hint">{field.hint}</span> : null}
        <div className="bf-age">
          <input
            type="text"
            name="age_from"
            inputMode="numeric"
            maxLength={3}
            pattern="[0-9]{1,3}"
            placeholder="от"
            aria-label="Возраст от"
          />
          <input
            type="text"
            name="age_to"
            inputMode="numeric"
            maxLength={3}
            pattern="[0-9]{1,3}"
            placeholder="до"
            aria-label="Возраст до"
          />
        </div>
      </div>
    );
  }

  const isInvalid = invalid.has(field.name);
  const errorId = `${field.name}-error`;
  // Карточки выбора — это группа, её подписывает не <label>, а обёртка.
  const isGroup = field.kind === "choices";

  const label = (
    <>
      {field.label}
      {field.required ? (
        <span className="bf-req" aria-hidden="true">
          {" *"}
        </span>
      ) : null}
    </>
  );

  return (
    <div
      className={isInvalid ? "bf-field is-invalid" : "bf-field"}
      role={isGroup ? "group" : undefined}
      aria-labelledby={isGroup ? `${field.name}-label` : undefined}
    >
      {isGroup ? (
        <span className="bf-field__label" id={`${field.name}-label`}>
          {label}
        </span>
      ) : (
        <label className="bf-field__label" htmlFor={field.name}>
          {label}
        </label>
      )}

      {field.hint ? <span className="bf-hint">{field.hint}</span> : null}

      {field.kind === "input" && (
        <input
          id={field.name}
          name={field.name}
          type={field.type ?? "text"}
          placeholder={field.placeholder}
          autoComplete={field.autoComplete}
          inputMode={field.inputMode}
          required={field.required}
          maxLength={field.maxLength}
          pattern={field.pattern}
          aria-invalid={isInvalid || undefined}
          aria-describedby={isInvalid && field.error ? errorId : undefined}
        />
      )}

      {field.kind === "textarea" && (
        <textarea
          id={field.name}
          name={field.name}
          rows={field.rows}
          placeholder={field.placeholder}
          required={field.required}
          maxLength={field.maxLength}
          aria-invalid={isInvalid || undefined}
          aria-describedby={isInvalid && field.error ? errorId : undefined}
        />
      )}

      {field.kind === "select" && (
        <select
          id={field.name}
          name={field.name}
          defaultValue=""
          required={field.required}
          aria-invalid={isInvalid || undefined}
          aria-describedby={isInvalid && field.error ? errorId : undefined}
        >
          <option value="" disabled>
            {field.placeholder ?? "Выберите"}
          </option>
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}

      {field.kind === "choices" && (
        <div className="bf-choices">
          {field.options.map((option, index) => {
            const id = `${field.name}-${index}`;
            return (
              <div className="bf-choice" key={option.value}>
                <input
                  type="radio"
                  id={id}
                  name={field.name}
                  value={option.value}
                  disabled={option.disabled}
                />
                <label htmlFor={id}>
                  <span className="bf-choice__mark" aria-hidden="true" />
                  <span>{option.label}</span>
                  {option.disabled ? <span className="bf-choice__soon">скоро</span> : null}
                </label>
              </div>
            );
          })}
        </div>
      )}

      {field.kind === "input" && field.link ? (
        <a className="bf-link" href={field.link.href} target="_blank" rel="noopener">
          {field.link.text}
        </a>
      ) : null}

      {isInvalid && field.error ? (
        <div className="bf-field__error" id={errorId}>
          {field.error}
        </div>
      ) : null}
    </div>
  );
}
