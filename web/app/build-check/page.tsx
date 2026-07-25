// Смоук-страница контрольной точки N1. Цель — доказать, что цепочка
// npm ci → next build → COPY в образ → FastAPI → Caddy → домен реально работает,
// и заодно показать перенесённые токены на живом компоненте.
// Ни на что не ссылается и никем не ссылается; удаляется на шаге N7.
import { NumberedField } from "@/components/NumberedField";

export default function BuildCheck() {
  return (
    <main
      style={{
        maxWidth: "var(--width-form)",
        margin: "0 auto",
        padding: "var(--space-4)",
      }}
    >
      <h1 style={{ fontSize: "var(--text-title)" }}>build-check</h1>
      <p style={{ color: "var(--muted)" }}>Next.js static export собран и отдаётся ядром.</p>

      <NumberedField num="01" label="Обычное поле" name="ok" defaultValue="" />
      <NumberedField
        num="02"
        label="Поле с ошибкой"
        name="bad"
        error="Заполните обязательное поле"
      />
    </main>
  );
}
