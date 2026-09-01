// eslint-config-next 16 отдаёт готовые flat-конфиги массивами, поэтому обёртка
// FlatCompat из @eslint/eslintrc не нужна (с ней конфиг вообще не грузится:
// валидатор eslintrc падает на циклической ссылке в plugins.react).
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const config = [
  // public/ — ещё не перенесённая статика прежнего фронта (vanilla HTML/CSS/JS).
  // Её не линтуем: она отдаётся как есть и уйдёт по мере переноса страниц.
  { ignores: ["out/**", ".next/**", "public/**", "next-env.d.ts"] },
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    rules: {
      // У нас output: "export" (см. next.config.ts) — статический экспорт без
      // клиентского роутера между страницами. Переходы на /admin.html и
      // /cabinet.html ведут на отдельные HTML-документы, которые отдаёт наш
      // сервер, а не на маршруты Next, поэтому замена на клиентскую навигацию,
      // которую предлагает это правило, тут неприменима. Полная перезагрузка
      // страницы через location.href сделана намеренно: выход из кабинета и
      // админки должен сбрасывать всё клиентское состояние, а клиентский
      // переход его как раз сохранил бы — то есть предлагаемое правилом
      // исправление сломало бы рабочее поведение.
      "@next/next/no-location-assign-relative-destination": "off",
    },
  },
];

export default config;
