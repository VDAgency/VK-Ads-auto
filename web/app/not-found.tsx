// Экспортируется в out/404.html. FastAPI StaticFiles с html=True отдаёт этот файл
// на несуществующие пути, сохраняя статус 404.
export default function NotFound() {
  return (
    <main>
      <h1>Страница не найдена</h1>
      <p>
        <a href="/">Вернуться на главную</a>
      </p>
    </main>
  );
}
