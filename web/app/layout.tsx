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
// Начертания НЕ перечисляем: обе гарнитуры переменные, и без списка весов
// next/font забирает один переменный файл вместо пяти статических. Замер на
// Slow 4G с четырёхкратным замедлением процессора показал, что пять файлов
// Onest плюс два JetBrains Mono откладывали отрисовку крупнейшего элемента.
const onest = Onest({
  subsets: ["cyrillic", "latin"],
  variable: "--font-onest",
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  subsets: ["cyrillic", "latin"],
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
