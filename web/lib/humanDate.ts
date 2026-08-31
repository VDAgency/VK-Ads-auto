// Человеческая дата для списков админки: «сегодня» / «вчера» / «12 августа»
// (spec 2026-08-31: даты — по-человечески, без времени в списке).
// Чистое форматирование, бизнес-логики не несёт.

const RU_MONTHS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** `isoOrDate` — ISO-строка из ответа ядра либо готовый `Date`. */
export function humanDate(isoOrDate: string | Date): string {
  const date = typeof isoOrDate === "string" ? new Date(isoOrDate) : isoOrDate;
  const now = new Date();
  const diffDays = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);

  if (diffDays === 0) return "сегодня";
  if (diffDays === 1) return "вчера";

  const day = date.getDate();
  const month = RU_MONTHS[date.getMonth()];
  if (date.getFullYear() !== now.getFullYear()) {
    return `${day} ${month} ${date.getFullYear()}`;
  }
  return `${day} ${month}`;
}
