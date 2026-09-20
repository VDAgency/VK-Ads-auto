"use client";

// Шаг 3 мастера — креатив (spec 2026-08-31-admin-cabinet-design-brief.md §6).
// Перенесено из прежнего `BriefCardView.tsx` как есть (перетаскивание, превью,
// проверки, счётчики) — задание прямо требует не переделывать эту часть.
// Отличие от прежнего поведения: кнопка здесь только переходит к подтверждению,
// саму отправку делает `ConfirmStep` после явного «Запустить» (Т3).

import { useEffect, useRef, useState } from "react";

import type { PickedFile } from "./types";
import { buildFileIssues, formatFileSize, pickFile, showFirstFrame } from "./fileUtils";

export function CreativeStep({
  picked,
  onPickedChange,
  title,
  onTitleChange,
  body,
  onBodyChange,
  hashtags,
  onHashtagsChange,
  hashtagsError,
  onContinue,
}: {
  picked: PickedFile | null;
  onPickedChange: (picked: PickedFile | null) => void;
  title: string;
  onTitleChange: (value: string) => void;
  body: string;
  onBodyChange: (value: string) => void;
  hashtags: string;
  onHashtagsChange: (value: string) => void;
  /** Причина отказа ядра по хэштегам (422 `hashtags_*`, задача 5) — приходит от
   * попытки запуска на шаге подтверждения; здесь только показ под полем, сама
   * ошибка узнаётся уже после отправки, не при наборе текста. */
  hashtagsError?: string | null;
  onContinue: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // Превью — objectURL, который нужно освободить при выборе нового файла и
  // при уходе с экрана, иначе адреса копятся в памяти вкладки.
  useEffect(() => {
    return () => {
      if (picked) URL.revokeObjectURL(picked.previewUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picked?.previewUrl]);

  async function selectFile(file: File) {
    const next = await pickFile(file);
    if (picked) URL.revokeObjectURL(picked.previewUrl);
    onPickedChange(next);
  }

  function clearFile() {
    if (picked) URL.revokeObjectURL(picked.previewUrl);
    onPickedChange(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  const fileIssues = picked ? buildFileIssues(picked) : [];
  const canContinue = Boolean(picked) && fileIssues.length === 0;

  return (
    <div>
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
              <span>{formatFileSize(picked.file.size)}</span>
            </p>
            <div className="adm-drop__file-actions">
              <button className="btn" type="button" onClick={() => fileInputRef.current?.click()}>
                Заменить файл
              </button>
              <button className="btn btn--ghost" type="button" onClick={clearFile}>
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
          onChange={(event) => onTitleChange(event.target.value)}
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
          onChange={(event) => onBodyChange(event.target.value)}
        />
        <p className="adm-panel__hint">{body.length} из 220</p>
      </div>

      <div className="form-field">
        <label htmlFor="cr-hashtags">Хэштеги</label>
        <input
          id="cr-hashtags"
          type="text"
          value={hashtags}
          onChange={(event) => onHashtagsChange(event.target.value)}
          aria-describedby="cr-hashtags-hint cr-hashtags-error"
        />
        <p className="adm-panel__hint" id="cr-hashtags-hint">
          Необязательно, через пробел или запятую, например: кофе утро
        </p>
        <div id="cr-hashtags-error" role="alert">
          {hashtagsError ? <p className="adm-drop__error">{hashtagsError}</p> : null}
        </div>
      </div>

      <div className="adm-actions">
        <button
          className="btn btn--primary"
          type="button"
          disabled={!canContinue}
          onClick={onContinue}
        >
          Продолжить
        </button>
      </div>
    </div>
  );
}
