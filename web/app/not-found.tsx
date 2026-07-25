import Link from "next/link";

// Экспортируется в out/404.html. FastAPI StaticFiles с html=True отдаёт этот файл
// на несуществующие пути, сохраняя статус 404.
export default function NotFound() {
  return (
    <main className="wrap">
      <h1>Страница не найдена</h1>
      <p>
        <Link href="/">Вернуться на главную</Link>
      </p>
    </main>
  );
}
