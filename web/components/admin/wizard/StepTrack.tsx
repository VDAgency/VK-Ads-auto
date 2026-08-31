// Дорожка мастера запуска: все шаги видны сразу, текущий раскрыт, пройденные
// сворачиваются в строку с выбранным значением и остаются кликабельными для
// возврата (spec 2026-08-31-admin-cabinet-design-brief.md §5, DESIGN.md §8).
//
// Пройденный шаг — всегда `button` (кликабелен, ведёт назад); шаг, до которого
// ещё не дошли, — статичный блок без обработчика, а не кнопка без действия
// (та же ошибка доступности, что уже однажды правили — `ui/Row.tsx`).

import type { ReactNode, RefObject } from "react";

import { STEP_TITLES, type WizardStepId } from "./types";

export function StepTrack({
  steps,
  current,
  summaries,
  onJump,
  headingRef,
  children,
}: {
  steps: WizardStepId[];
  current: WizardStepId;
  /** Краткое значение, выбранное на пройденном шаге (например, название кабинета). */
  summaries: Partial<Record<WizardStepId, string>>;
  onJump: (step: WizardStepId) => void;
  /** Заголовок текущего шага — сюда переходит фокус при смене шага. */
  headingRef: RefObject<HTMLHeadingElement | null>;
  /** Содержимое текущего (раскрытого) шага. */
  children: ReactNode;
}) {
  const currentIndex = steps.indexOf(current);

  return (
    <ol className="wiz">
      {steps.map((step, index) => {
        const isDone = index < currentIndex;
        const isCurrent = index === currentIndex;
        const status = isDone ? "done" : isCurrent ? "current" : "todo";
        const summary = summaries[step];

        return (
          <li className={`wiz-step is-${status}`} key={step}>
            {isDone ? (
              <button
                type="button"
                className="wiz-step__head"
                onClick={() => onJump(step)}
                aria-label={
                  summary
                    ? `${STEP_TITLES[step]}: ${summary}. Вернуться и изменить`
                    : STEP_TITLES[step]
                }
              >
                <span className="wiz-step__dot" aria-hidden="true">
                  ✓
                </span>
                <span className="wiz-step__label">
                  <span className="wiz-step__title">{STEP_TITLES[step]}</span>
                  {summary ? <span className="wiz-step__value">{summary}</span> : null}
                </span>
              </button>
            ) : (
              <div className="wiz-step__head wiz-step__head--static">
                <span className="wiz-step__dot" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="wiz-step__label">
                  {isCurrent ? (
                    <h3 className="wiz-step__title" tabIndex={-1} ref={headingRef}>
                      {STEP_TITLES[step]}
                    </h3>
                  ) : (
                    <span className="wiz-step__title">{STEP_TITLES[step]}</span>
                  )}
                </span>
              </div>
            )}

            {isCurrent ? <div className="wiz-step__body">{children}</div> : null}
          </li>
        );
      })}
    </ol>
  );
}
