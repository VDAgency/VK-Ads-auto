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

// --- реквизиты для документов (зеркало `core/api/v1/cabinet.py`, spec §E) --

/** Поля реквизитов — тело `PUT /cabinet/bank-details` и форма сохранённых данных. */
export type BankDetailsInput = {
  payer_name: string;
  bank_name: string;
  bik: string;
  settlement_account: string;
  correspondent_account: string;
};

export type BankDetailsOut = BankDetailsInput;

/** Ответ `GET /cabinet/bank-details`: сохранённые реквизиты (или их нет) + подсказка из брифа. */
export type BankDetailsView = {
  bank_details: BankDetailsOut | null;
  brief_hint: string | null;
};

export type BankDetailsSaveOut = { bank_details: BankDetailsOut };

/** Ошибки по полям из 422-ответа `PUT /cabinet/bank-details` — код на поле. */
export type BankDetailsFieldErrors = Record<string, string>;

/** Токен добавляется в путь, только если он есть: клиент, пришедший по
 * ссылке входа без cookie-сессии (`request-link`, password уже установлен —
 * `CabinetPage` тогда не выдаёт cookie), иначе останется без доступа к своему
 * же кабинету на этой странице (та же граница, что `GET /cabinet?token=`). */
function withToken(path: string, token?: string | null): string {
  return token ? `${path}?token=${encodeURIComponent(token)}` : path;
}

export function getBankDetails(token?: string | null): Promise<BankDetailsView> {
  return apiFetch<BankDetailsView>(withToken("/cabinet/bank-details", token));
}

export function saveBankDetails(
  input: BankDetailsInput,
  token?: string | null,
): Promise<BankDetailsSaveOut> {
  return apiFetch<BankDetailsSaveOut>(withToken("/cabinet/bank-details", token), {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

/** Извлечь пофайловые ошибки из `ApiError` 422, если ответ имел форму `{errors: {...}}`. */
export function bankDetailsFieldErrors(error: unknown): BankDetailsFieldErrors | null {
  if (
    error instanceof ApiError &&
    error.status === 422 &&
    error.detail &&
    typeof error.detail === "object"
  ) {
    const errors = (error.detail as { errors?: unknown }).errors;
    if (errors && typeof errors === "object") {
      return errors as BankDetailsFieldErrors;
    }
  }
  return null;
}
