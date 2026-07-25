"use client";

import { type InputHTMLAttributes, useId } from "react";

type NumberedFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id"> & {
  /**
   * Номер поля («01», «02», …). Не украшение: по этому номеру оператор правит
   * сводку в боте форматом `номер.значение` (PROJECT.md §4.1.6), поэтому
   * нумерация обязана совпадать с порядком в services/brief_fields.py.
   */
  num: string;
  label: string;
  /** Текст ошибки: задаёт состояние поля и связывается через aria-describedby. */
  error?: string;
};

/**
 * Сигнатурное поле дизайн-системы: моно-маркер NN + лейбл + инпут.
 * Стили — в app/globals.css (класс `.numbered-field`).
 */
export function NumberedField({ num, label, error, ...input }: NumberedFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;

  return (
    <div className="numbered-field" data-invalid={error ? "true" : undefined}>
      <span className="numbered-field__num" aria-hidden="true">
        {num}
      </span>
      <label className="numbered-field__label" htmlFor={id}>
        {label}
      </label>
      <input
        {...input}
        id={id}
        className="numbered-field__input"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? (
        <p className="numbered-field__error" id={errorId}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
