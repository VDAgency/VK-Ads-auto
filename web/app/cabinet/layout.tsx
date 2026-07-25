import type { Metadata } from "next";
import type { ReactNode } from "react";

// Сама страница кабинета — клиентский компонент (ходит в API из браузера),
// а клиентские компоненты не могут экспортировать metadata. Поэтому заголовок
// задаётся здесь, в layout маршрута.
export const metadata: Metadata = {
  title: "Личный кабинет — VK-Ads-auto",
};

export default function CabinetLayout({ children }: { children: ReactNode }) {
  return children;
}
