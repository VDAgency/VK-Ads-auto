import type { Metadata } from "next";
import type { ReactNode } from "react";

// Базовые стили и токены дизайн-системы. Раньше лежали в public/ и
// подключались тегом <link>, потому что их делили с ещё не перенесёнными
// страницами. Все страницы перенесены — файл живёт в дереве приложения и
// собирается вместе с остальным.
import "./styles.css";

export const metadata: Metadata = {
  title: "VK Ads auto",
  description: "Автоматизация запуска рекламы в VK Ads",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
