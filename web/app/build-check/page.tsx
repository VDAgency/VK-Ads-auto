// Смоук-страница контрольной точки миграции. Цель — доказать, что цепочка
// npm ci → next build → COPY в образ → FastAPI → Caddy → домен реально работает.
// Ни на что не ссылается и никем не ссылается; удаляется на шаге N7.
export default function BuildCheck() {
  return (
    <main className="wrap">
      <h1>build-check</h1>
      <p className="lead">Next.js static export собран и отдаётся ядром.</p>
    </main>
  );
}
