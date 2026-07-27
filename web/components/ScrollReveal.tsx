"use client";

import { useEffect } from "react";

/**
 * Мягкое появление блоков `.reveal` при прокрутке.
 *
 * Первый экран показывается сразу и не наблюдается вовсе. Прежняя версия
 * отдавала наблюдателю все блоки с порогом 0.14 и отрицательным `rootMargin`,
 * из-за чего при высоте вьюпорта 732 px кнопки героя не пересекали область
 * наблюдения и оставались невидимыми до прокрутки — на лендинге, живущем с
 * холодного трафика, это стоило всей конверсии.
 *
 * `prefers-reduced-motion` уважается: блоки показываются целиком, без движения.
 */
export function ScrollReveal() {
  useEffect(() => {
    const items = Array.from(document.querySelectorAll<HTMLElement>(".reveal"));
    if (items.length === 0) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      items.forEach((el) => el.classList.add("is-visible"));
      return;
    }

    // Всё, что попадает в первый экран, показываем немедленно.
    const deferred = items.filter((el) => {
      if (el.getBoundingClientRect().top < window.innerHeight) {
        el.classList.add("is-visible");
        return false;
      }
      return true;
    });

    if (deferred.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      // Порог 0: достаточно любого пересечения. Небольшой отрицательный отступ
      // снизу — чтобы блок появлялся, войдя в кадр, а не касаясь его краем.
      { threshold: 0, rootMargin: "0px 0px -12% 0px" },
    );

    deferred.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return null;
}
