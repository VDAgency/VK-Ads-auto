// Смоук-страница контрольной точки N1. Единственная цель — доказать, что цепочка
// npm ci → next build → COPY в образ → FastAPI → Caddy → домен реально работает,
// до того как на Next.js переедет хоть одна настоящая страница.
// Ни на что не ссылается и никем не ссылается; удаляется на шаге N7.
export default function BuildCheck() {
  return (
    <main>
      <h1>build-check</h1>
      <p>Next.js static export собран и отдаётся ядром.</p>
    </main>
  );
}
