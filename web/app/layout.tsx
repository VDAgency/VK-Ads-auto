import type { Metadata } from "next";
import { JetBrains_Mono, Onest } from "next/font/google";
import type { ReactNode } from "react";

// Токены дизайн-системы — единственный источник значений (см. DESIGN.md).
// Импортируются первыми: styles.css и надстройки зон читают их переменные.
import "./tokens.css";
import "./styles.css";

// Onest — кириллица родная, а не добавленная позже; переменное начертание
// закрывает всю шкалу весов одним файлом. JetBrains Mono — номера полей, ID и
// суммы: номер поля рабочий, по нему идут правки `номер.значение` в боте.
//
// next/font скачивает и хостит файлы на сборке, поэтому страница не зависит
// от доступности CDN Google в рантайме — для российских пользователей это не
// косметика. Заодно уходит скачок вёрстки при подмене шрифта.
const onest = Onest({
  subsets: ["cyrillic", "latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-onest",
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  subsets: ["cyrillic", "latin"],
  weight: ["400", "600"],
  variable: "--font-mono-jb",
  display: "swap",
});

export const metadata: Metadata = {
  title: "VK Ads auto",
  description: "Автоматизация запуска рекламы в VK Ads",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru" className={`${onest.variable} ${jetBrainsMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
