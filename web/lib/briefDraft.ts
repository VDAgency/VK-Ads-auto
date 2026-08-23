/**
 * Черновик формы брифа в localStorage — общий для `BriefForm` и `BriefGoalSurface`.
 *
 * Раньше жил только внутри `BriefForm.tsx`: обычные поля восстанавливаются им
 * самим через прямой обход DOM. Вкладки цели/площадки (`BriefGoalSurface`)
 * управляют своим кусочком формы через React-состояние, а не через DOM
 * напрямую (иначе переключение вкладки после восстановления черновика било бы
 * мимо активной панели) — поэтому им нужен тот же черновик, но independent
 * доступ к нему при первом рендере.
 */

// Дублирует `BriefVariant` из `@/components/BriefForm` (не импортируем оттуда,
// чтобы не заводить цикл: BriefForm сам импортирует эти функции отсюда).
type BriefVariant = "individual" | "community";

/** Ключ черновика: у каждого варианта брифа свой. */
export function draftKey(variant: BriefVariant): string {
  return `vk-ads-auto:brief-draft:${variant}`;
}

/** Прочитать черновик. Любая ошибка хранилища или отсутствие `window` (SSR) — «черновика нет». */
export function readDraft(variant: BriefVariant): Record<string, string> {
  if (typeof window === "undefined") return {};
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
