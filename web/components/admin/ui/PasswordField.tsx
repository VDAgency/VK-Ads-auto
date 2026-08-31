"use client";

import { useId, useState } from "react";

// Поле пароля с показом/скрытием — та же пластика, что в LoginModal (значок
// глаза, переключатель `aria-pressed`), но собственный компонент: LoginModal
// живёт в бандле лендинга (landing.css), а админка — отдельная зона.

export function PasswordField({
  label,
  value,
  onChange,
  autoComplete = "current-password",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: "current-password" | "new-password";
}) {
  const [visible, setVisible] = useState(false);
  const id = useId();

  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      <div className="adm-pw">
        <input
          id={id}
          name="password"
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          required
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          className={visible ? "adm-pw__eye is-on" : "adm-pw__eye"}
          type="button"
          aria-label={visible ? "Скрыть пароль" : "Показать пароль"}
          aria-pressed={visible}
          onClick={() => setVisible((value) => !value)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"
              stroke="currentColor"
              strokeWidth="1.8"
            />
            <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
          </svg>
        </button>
      </div>
    </div>
  );
}
