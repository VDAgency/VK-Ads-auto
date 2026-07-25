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
];

export default config;
