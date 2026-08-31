"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { adminFetch, VARIANT_RU, type BriefCard, type Flash } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { StatusBadge } from "./ui/Badge";
import { BackLink } from "./ui/BackLink";
import { ErrorState } from "./ui/ErrorState";
import { SkeletonCard } from "./ui/Skeleton";

/** Разбор правок формата `номер.значение`, по одной на строку. */
function parseEdits(text: string): Record<string, string> {
  const edits: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^\s*(\d+)\s*\.\s*(.+?)\s*$/);
    if (match) edits[match[1]] = match[2];
  }
  return edits;
}

/** Файл → base64 без префикса data:. */
function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

type FileKind = "photo" | "video";

/** Итог разбора выбранного файла — то, что нужно и для превью, и для проверок. */
type PickedFile = {
  file: File;
  previewUrl: string;
  kind: FileKind | null;
  width: number;
  height: number;
};

// Зеркало ограничений ядра — только для быстрой обратной связи до отправки;
// последнее слово всегда за ядром (services/creative_intake.py, creative_validate.py).
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const MIN_IMAGE_SIDE = 600;

function isAllowedImageType(file: File): boolean {
  return file.type === "image/jpeg" || file.type === "image/jpg" || file.type === "image/png";
}

function isAllowedVideoType(file: File): boolean {
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
async function pickFile(file: File): Promise<PickedFile> {
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
function buildFileIssues(picked: PickedFile): string[] {
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
function showFirstFrame(event: React.SyntheticEvent<HTMLVideoElement>): void {
  const video = event.currentTarget;
  try {
    video.currentTime = 0.01;
  } catch {
    // Не критично: плеер всё равно рабочий, просто без кадра до воспроизведения.
  }
}

export function BriefCardView({
  id,
  onBack,
  onFlash,
}: {
  id: number;
  onBack: () => void;
  onFlash: (flash: Flash) => void;
}) {
  const [resource, retryResource] = useAdminResource<BriefCard>(`/briefs/${id}`, [id]);
  const [card, setCard] = useState<BriefCard | null>(null);
  const [showEdits, setShowEdits] = useState(false);
  const [showCreative, setShowCreative] = useState(false);
  const [edits, setEdits] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [picked, setPicked] = useState<PickedFile | null>(null);
  const [fileIssues, setFileIssues] = useState<string[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [sending, setSending] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Держим карточку отдельным состоянием: после правок/загрузки креатива её
  // обновляет ответ мутации напрямую, не дожидаясь нового GET через хук.
  useEffect(() => {
    if (resource.status !== "ready") return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCard(resource.data);
  }, [resource]);

  // Превью — objectURL, который нужно освободить при выборе нового файла и
  // при уходе с экрана, иначе адреса копятся в памяти вкладки.
  useEffect(() => {
    return () => {
      if (picked) URL.revokeObjectURL(picked.previewUrl);
    };
  }, [picked]);

  async function selectFile(file: File) {
    const next = await pickFile(file);
    setPicked((prevPicked) => {
      if (prevPicked) URL.revokeObjectURL(prevPicked.previewUrl);
      return next;
    });
    setFileIssues(buildFileIssues(next));
  }

  function clearFile() {
    setPicked((prevPicked) => {
      if (prevPicked) URL.revokeObjectURL(prevPicked.previewUrl);
      return null;
    });
    setFileIssues([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function applyEdits() {
    const parsed = parseEdits(edits);
    if (!Object.keys(parsed).length) {
      onFlash({ text: "Не понял правки. Формат: номер.значение", ok: false });
      return;
    }
    try {
      const data = await adminFetch<BriefCard>(`/briefs/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ edits: parsed }),
      });
      setCard(data);
      onFlash({
        text:
          "Правки применены." +
          (data.unknown?.length ? ` Неизвестные номера: ${data.unknown.join(", ")}` : ""),
        ok: true,
      });
    } catch {
      onFlash({ text: "Не удалось применить правки.", ok: false });
    }
  }

  async function launchWithoutCreative() {
    // Продвижение готового объекта: креатива нет, запуск — отдельным действием.
    onFlash({ text: "Запуск…", ok: true });
    try {
      const data = await adminFetch<{ message: string }>(`/briefs/${id}/launch`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      // Запуск необратим — подтверждение остаётся на экране, не исчезает само.
      onFlash({ text: data.message, ok: true, persistent: true });
      setCard(await adminFetch<BriefCard>(`/briefs/${id}`));
    } catch (error) {
      let reason = "Запустить не вышло.";
      if (error instanceof ApiError) {
        const detail = error.detail as { missing?: string[] } | undefined;
        if (detail?.missing) reason = `Бриф неполный: ${detail.missing.join(", ")}`;
      }
      onFlash({ text: reason, ok: false });
    }
  }

  async function uploadCreative() {
    if (!picked) {
      onFlash({ text: "Выберите фото или видео.", ok: false });
      return;
    }
    if (fileIssues.length || picked.kind === null) {
      onFlash({ text: "Сначала поправьте файл — ограничения показаны над кнопкой.", ok: false });
      return;
    }

    setSending(true);
    try {
      const b64 = await readFileBase64(picked.file);
      const data = await adminFetch<{ message: string }>(`/briefs/${id}/creative`, {
        method: "POST",
        body: JSON.stringify({
          media_b64: b64,
          media_type: picked.kind,
          width: picked.width,
          height: picked.height,
          title,
          body,
        }),
      });
      // Отправка креатива запускает кампанию — необратимо, подтверждение остаётся.
      onFlash({ text: data.message, ok: true, persistent: true });
      const fresh = await adminFetch<BriefCard>(`/briefs/${id}`);
      setCard(fresh);
    } catch (error) {
      let reason = "Креатив не принят.";
      if (error instanceof ApiError) {
        const detail = error.detail as { issues?: string[]; missing?: string[] } | undefined;
        if (detail?.issues) reason = detail.issues.join(" ");
        else if (detail?.missing) reason = `Бриф неполный: ${detail.missing.join(", ")}`;
      }
      onFlash({ text: reason, ok: false });
    } finally {
      setSending(false);
    }
  }

  if (resource.status === "loading" || !card) {
    return (
      <>
        <BackLink label="← к брифам" onClick={onBack} />
        <SkeletonCard />
      </>
    );
  }

  if (resource.status === "error") {
    return (
      <>
        <BackLink label="← к брифам" onClick={onBack} />
        <ErrorState message="Не удалось открыть бриф." onRetry={retryResource} />
      </>
    );
  }

  return (
    <>
      <BackLink label="← к брифам" onClick={onBack} />

      <div className="adm-card">
        <div className="adm-card__head">
          <h2>
            Бриф <span className="adm-mono">№{card.brief_id}</span>
          </h2>
          <span className="badge">{VARIANT_RU[card.variant] ?? card.variant}</span>
          <StatusBadge status={card.status} />
          <span className={card.has_creative ? "badge badge--accent" : "badge badge--warn"}>
            {card.has_creative ? "креатив загружен" : "креатива нет"}
          </span>
        </div>
        <p className="adm-card__contacts">
          {card.client.full_name || "Без имени"}
          {" · "}
          {[card.client.email, card.client.phone, card.client.telegram].filter(Boolean).join(" · ")}
        </p>

        {card.surface_title ? (
          // Клиент выбирает площадку словами; показываем, как её понял разбор
          // брифа, чтобы ошибка в поле была видна до запуска, а не после.
          <p className="adm-card__surface">
            Площадка по разбору брифа: <strong>{card.surface_title}</strong>
          </p>
        ) : null}

        {/* Таблица, а не сплошной список: номер — рабочий инструмент, по нему
            идут правки `номер.значение`, и он обязан находиться взглядом. */}
        <dl className="adm-fields">
          {card.fields.map((field) => (
            <div className="adm-field" key={field.n}>
              <dt className="adm-field__n">{field.n}</dt>
              <dd className="adm-field__label">{field.label}</dd>
              <dd className={field.value ? "adm-field__value" : "adm-field__value is-empty"}>
                {field.value || "не заполнено"}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="adm-actions">
        <button
          className="btn"
          id="edit-toggle"
          type="button"
          onClick={() => setShowEdits((value) => !value)}
        >
          Внести правки
        </button>
        {card.surface_needs_creative === false ? (
          // Объявлением служит сам пост, клип или трек — просить картинку не за чем.
          <button
            className="btn btn--primary"
            id="launch-no-creative"
            type="button"
            onClick={() => void launchWithoutCreative()}
          >
            Запустить без креатива
          </button>
        ) : (
          <button
            className="btn btn--primary"
            id="creative-toggle"
            type="button"
            onClick={() => setShowCreative((value) => !value)}
          >
            Загрузить креатив
          </button>
        )}
      </div>

      <div className="adm-panel" id="edit-box" hidden={!showEdits}>
        <p className="adm-panel__hint">
          {"Формат: номер.значение, по одной правке на строку. Например:\n" +
            (card.fields[0] ? `${card.fields[0].n}. новое значение\n` : "") +
            (card.fields[1] ? `${card.fields[1].n}. новое значение` : "")}
        </p>
        <textarea
          id="edits"
          rows={4}
          value={edits}
          onChange={(event) => setEdits(event.target.value)}
        />
        <button
          className="btn btn--primary"
          id="edit-send"
          type="button"
          onClick={() => void applyEdits()}
        >
          Применить
        </button>
      </div>

      <div className="adm-panel" id="creative-box" hidden={!showCreative}>
        <div className="form-field">
          <label id="cr-file-label">Фото или видео</label>

          {/* Зона — не button/label поверх input, а сама фокусируемая цель:
              так одинаково работают клик, Enter/Пробел и перетаскивание. */}
          <div
            className={isDragOver ? "adm-drop adm-drop--over" : "adm-drop"}
            role="button"
            tabIndex={0}
            aria-labelledby="cr-file-label"
            aria-describedby="cr-file-hint cr-file-errors"
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragOver(true);
            }}
            onDragLeave={() => setIsDragOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragOver(false);
              const dropped = event.dataTransfer.files?.[0];
              if (dropped) void selectFile(dropped);
            }}
          >
            <p className="adm-drop__title">Перетащите файл сюда или нажмите, чтобы выбрать</p>
            <p className="adm-drop__sub">Фото — JPG или PNG. Видео — MP4.</p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            id="cr-file"
            className="visually-hidden"
            tabIndex={-1}
            accept="image/jpeg,image/png,video/mp4"
            onChange={(event) => {
              const chosen = event.target.files?.[0];
              if (chosen) void selectFile(chosen);
            }}
          />
          <p className="adm-panel__hint" id="cr-file-hint">
            Минимум для фото — 600×600 px. Файл — до 25 МБ.
          </p>
          <div id="cr-file-errors" role="alert">
            {fileIssues.map((issue) => (
              <p className="adm-drop__error" key={issue}>
                {issue}
              </p>
            ))}
          </div>

          {picked ? (
            <div className="adm-drop__preview">
              {picked.kind === "video" ? (
                <video
                  className="adm-drop__media"
                  src={picked.previewUrl}
                  controls
                  preload="metadata"
                  onLoadedMetadata={showFirstFrame}
                />
              ) : (
                // Превью локального blob-URL, не сетевой ассет — `next/image` тут
                // ничего не оптимизирует (оптимизация и так выключена конфигом),
                // только требует лишние поля.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  className="adm-drop__media"
                  src={picked.previewUrl}
                  alt={`Превью файла «${picked.file.name}»`}
                />
              )}
              <p className="adm-drop__meta">
                <span>{picked.file.name}</span>
                <span>{(picked.file.size / (1024 * 1024)).toFixed(1)} МБ</span>
              </p>
              <div className="adm-drop__file-actions">
                <button
                  className="btn"
                  type="button"
                  disabled={sending}
                  onClick={() => fileInputRef.current?.click()}
                >
                  Заменить файл
                </button>
                <button
                  className="btn btn--ghost"
                  type="button"
                  disabled={sending}
                  onClick={clearFile}
                >
                  Удалить
                </button>
              </div>
            </div>
          ) : null}
        </div>

        <div className="form-field">
          <label htmlFor="cr-title">Заголовок</label>
          <input
            id="cr-title"
            type="text"
            maxLength={40}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <p className="adm-panel__hint">{title.length} из 40</p>
        </div>
        <div className="form-field">
          <label htmlFor="cr-body">Текст</label>
          <textarea
            id="cr-body"
            rows={3}
            maxLength={220}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
          <p className="adm-panel__hint">{body.length} из 220</p>
        </div>
        {/* Единственное необратимое действие в панели — отделено и названо прямо. */}
        <button
          className="btn btn--primary"
          id="cr-send"
          type="button"
          disabled={sending || !picked || fileIssues.length > 0}
          onClick={() => void uploadCreative()}
        >
          {sending ? "Отправляем…" : "Отправить и запустить кампанию"}
        </button>
      </div>
    </>
  );
}
