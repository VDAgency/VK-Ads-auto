"use client";

type BackLinkProps = {
  children: React.ReactNode;
  className?: string;
};

/**
 * Ссылка «назад» по истории браузера.
 *
 * В прежней вёрстке это был `href="javascript:history.back()"`. Такой href — и
 * дыра под инъекцию, и нерабочая ссылка при отключённом JS, поэтому здесь
 * обычная кнопка, стилизованная под ссылку: поведение то же, разметка честная.
 */
export function BackLink({ children, className }: BackLinkProps) {
  return (
    <button type="button" className={className} onClick={() => history.back()}>
      {children}
    </button>
  );
}
