// Работа с локальным файлом креатива: разбор, проверки, кодирование для отправки.
// Вынесено из прежнего `BriefCardView.tsx` без переделки логики (задание:
// «Уже сделан... перенеси как есть, не переделывай») — только раздельно по
// файлам, которые его используют (`CreativeStep` — выбор и превью,
// `ConfirmStep` — кодирование в base64 непосредственно перед отправкой).

import type { FileKind, PickedFile } from "./types";

// Зеркало ограничений ядра — только для быстрой обратной связи до отправки;
// последнее слово всегда за ядром (services/creative_intake.py, creative_validate.py).
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const MIN_IMAGE_SIDE = 600;

export function isAllowedImageType(file: File): boolean {
  return file.type === "image/jpeg" || file.type === "image/jpg" || file.type === "image/png";
}

export function isAllowedVideoType(file: File): boolean {
  return file.type === "video/mp4";
}

/** Размеры изображения через `Image`. */
function readImageDimensions(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve({ width: 0, height: 0 });
    img.src = url;
  });
}

/** Размеры видео через `<video>` и `videoWidth`/`videoHeight`. */
function readVideoDimensions(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.onloadedmetadata = () => resolve({ width: video.videoWidth, height: video.videoHeight });
    video.onerror = () => resolve({ width: 0, height: 0 });
    video.src = url;
  });
}

/** Разобрать выбранный/перетащенный файл: тип, превью, размеры. */
export async function pickFile(file: File): Promise<PickedFile> {
  const previewUrl = URL.createObjectURL(file);
  const kind: FileKind | null = file.type.startsWith("image/")
    ? "photo"
    : file.type.startsWith("video/")
      ? "video"
      : null;
  const { width, height } =
    kind === "photo"
      ? await readImageDimensions(previewUrl)
      : kind === "video"
        ? await readVideoDimensions(previewUrl)
        : { width: 0, height: 0 };
  return { file, previewUrl, kind, width, height };
}

/** Проверки до отправки, человеческим языком — ровно то, что реально проверяет
 * ядро (25 МБ / формат и минимум 600×600 у фото / mp4 у видео), не больше. */
export function buildFileIssues(picked: PickedFile): string[] {
  const issues: string[] = [];
  const sizeMb = (picked.file.size / (1024 * 1024)).toFixed(1);

  if (picked.file.size > MAX_UPLOAD_BYTES) {
    issues.push(`Файл весит ${sizeMb} МБ — это больше, чем можно (до 25 МБ). Нужен файл поменьше.`);
  }

  if (picked.kind === null) {
    issues.push("Такой файл не подходит — нужно фото или видео.");
    return issues;
  }

  if (picked.kind === "photo") {
    if (!isAllowedImageType(picked.file)) {
      issues.push("Такой формат картинки не подходит — нужен JPG или PNG.");
    } else if (picked.width < MIN_IMAGE_SIDE || picked.height < MIN_IMAGE_SIDE) {
      issues.push(
        `Изображение ${picked.width}×${picked.height} px — маловато. Нужно не меньше ${MIN_IMAGE_SIDE}×${MIN_IMAGE_SIDE} px.`,
      );
    }
  } else if (!isAllowedVideoType(picked.file)) {
    issues.push("Такой формат видео не подходит — нужен MP4.");
  }

  return issues;
}

/** Первый кадр видео как превью: перематываем на долю секунды, иначе плеер
 * до нажатия «play» показывает чёрный кадр в части браузеров. */
export function showFirstFrame(event: React.SyntheticEvent<HTMLVideoElement>): void {
  const video = event.currentTarget;
  try {
    video.currentTime = 0.01;
  } catch {
    // Не критично: плеер всё равно рабочий, просто без кадра до воспроизведения.
  }
}

/** Человеческий размер файла: до 1 МБ — в килобайтах (без дробной части),
 * иначе — в мегабайтах с одним знаком после запятой. Раньше маленькие файлы
 * (например, 19 КБ) всегда переводились в МБ и округлялись до «0.0 МБ» —
 * бесполезная подпись (найденный баг). */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

/** Файл → base64 без префикса `data:`. */
export function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
