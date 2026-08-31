// Строка списка. Кликабельная строка — всегда `button`, никогда `div` с
// обработчиком (эта ошибка в проекте уже исправлялась однажды — CLAUDE.md).
// Строка без действия (например, «ждём бриф» без ссылки открыть) рендерится
// как статичный блок `.adm-row--static`, а не как кнопка без обработчика:
// нефункциональный, но фокусируемый элемент — сам по себе дефект доступности.

import type { ReactNode } from "react";

export function Row({
  title,
  subtitle,
  badge,
  extra,
  onClick,
}: {
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  /** Дополнительный бейдж слева от основного (например, срок ожидания). */
  extra?: ReactNode;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="adm-row__main">
        <span className="adm-row__title">{title}</span>
        {subtitle ? <span className="adm-row__meta">{subtitle}</span> : null}
      </span>
      {badge || extra ? (
        <span className="adm-row__side">
          {extra}
          {badge}
        </span>
      ) : null}
    </>
  );

  if (!onClick) {
    return <div className="adm-row adm-row--static">{content}</div>;
  }

  return (
    <button className="adm-row" type="button" onClick={onClick}>
      {content}
    </button>
  );
}
