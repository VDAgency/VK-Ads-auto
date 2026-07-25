"use client";

import { useEffect } from "react";

/**
 * Мягкое появление блоков `.reveal` при прокрутке.
 *
 * Перенос прежнего инлайнового скрипта лендинга. Уважает
 * `prefers-reduced-motion`: при включённой настройке (или без поддержки
 * IntersectionObserver) блоки сразу показываются целиком, без анимации.
 *
 * Скрытие до появления включает класс `js` на <html> — он ставится синхронным
 * инлайновым скриптом в разметке страницы, чтобы без JS контент оставался
 * видимым (прогрессивное улучшение).
 */
export function ScrollReveal() {
  useEffect(() => {
    const items = document.querySelectorAll<HTMLElement>(".reveal");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduce || !("IntersectionObserver" in window)) {
      items.forEach((el) => el.classList.add("is-visible"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.14, rootMargin: "0px 0px -8% 0px" },
    );

    items.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return null;
}
