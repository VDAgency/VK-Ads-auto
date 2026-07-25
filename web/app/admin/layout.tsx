import type { Metadata } from "next";
import type { ReactNode } from "react";

// Страница админки — клиентский компонент, поэтому заголовок задаётся здесь.
export const metadata: Metadata = {
  title: "Админ-панель — VK-Ads-auto",
};

export default function AdminLayout({ children }: { children: ReactNode }) {
  return children;
}
