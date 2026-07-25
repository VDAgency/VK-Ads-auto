import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "VK Ads auto",
  description: "Автоматизация запуска рекламы в VK Ads",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>
        {/* Базовые стили прежнего фронта. Файл лежит в public/ и отдаётся по тому
            же адресу, что и раньше, — так перенесённые страницы гарантированно
            выглядят как до миграции, а не «похоже». Сюда же ходят ещё не
            перенесённые страницы из public/. Уйдёт вместе с ними на дизайн-этапе.
            precedence нужен, чтобы React поднял тег в <head> и дедуплицировал. */}
        {/* eslint-disable-next-line @next/next/no-css-tags -- файл общий с ещё не
            перенесёнными страницами в public/; импортировать его как модуль значило
            бы держать две копии, которые разъедутся. Уйдёт вместе с public/ на N7. */}
        <link rel="stylesheet" href="/styles.css" precedence="default" />
        {children}
      </body>
    </html>
  );
}
