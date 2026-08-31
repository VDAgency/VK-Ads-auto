"use client";

// Сегментный переключатель — вынесен из LoginModal (см. `web/components/LoginModal.tsx`),
// который DESIGN.md называет эталоном пластики. Разметка и классы те же (`.seg`,
// `.seg__btn`), только вынесены из-под конкретной формы входа: теперь их видит
// и модалка входа, и фильтр «Ждём / Пришли» в брифах.

export type SegmentedOption<T extends string> = { value: T; label: string };

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
}: {
  value: T;
  onChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  ariaLabel: string;
}) {
  return (
    <div className="seg" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={option.value === value ? "seg__btn is-active" : "seg__btn"}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
