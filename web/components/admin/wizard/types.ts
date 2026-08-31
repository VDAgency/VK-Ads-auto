// Общие типы мастера запуска — вынесены отдельно, чтобы StepTrack и все шаги
// ссылались на один и тот же список, а не на пять локальных копий.

import type { AdAccount } from "@/lib/adminApi";

/** Шаги ровно как в боте (spec 2026-08-31 §5): поля → кабинет → цель → креатив →
 * подтверждение. «Цель»/«Креатив» пропускаются для площадок без креатива —
 * `stepsFor()` в LaunchWizard.tsx решает это по `surface_needs_creative`. */
export type WizardStepId = "fields" | "cabinet" | "goal" | "creative" | "confirm";

export const STEP_TITLES: Record<WizardStepId, string> = {
  fields: "Поля и правки",
  cabinet: "Рекламный кабинет",
  goal: "Цель рекламы",
  creative: "Креатив",
  confirm: "Подтверждение",
};

export type FileKind = "photo" | "video";

/** Итог разбора выбранного файла креатива — нужен и для превью, и для проверок,
 * и для итоговой отправки на шаге подтверждения. */
export type PickedFile = {
  file: File;
  previewUrl: string;
  kind: FileKind | null;
  width: number;
  height: number;
};

/** Данные, накопленные мастером к моменту подтверждения запуска. */
export type WizardChoice = {
  account: AdAccount | null;
  goalCode: string | null;
  goalTitle: string | null;
  picked: PickedFile | null;
  title: string;
  body: string;
};
