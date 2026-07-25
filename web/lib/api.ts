// Единственная точка обращения фронта к внутреннему API ядра (/api/v1).
//
// Здесь только транспорт. Никакой бизнес-логики: она живёт в ядре
// (CLAUDE.md §1.3, PROJECT.md решение 3.4). Статический экспорт Next не имеет
// рантайма — route handlers, server actions и cookies() запрещены самой
// сборкой, поэтому все вызовы уходят из браузера.

/** Ошибка API. `detail` — содержимое поля `detail` из ответа ядра, если оно было. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(`API ${status}`);
    this.name = "ApiError";
  }
}

/**
 * Запрос к `/api/v1<path>`.
 *
 * Cookie сессии кабинета и админки уходят автоматически
 * (`credentials: "same-origin"`). Бросает `ApiError` на любой не-2xx:
 * 401 — нет сессии, 422 — незаполненные поля (список в `detail.missing`),
 * 429 — превышен лимит запросов.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  // FormData сам проставляет multipart-границу, поэтому Content-Type
  // навязываем только строковому телу (JSON).
  if (typeof init?.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/api/v1${path}`, {
    credentials: "same-origin",
    ...init,
    headers,
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
