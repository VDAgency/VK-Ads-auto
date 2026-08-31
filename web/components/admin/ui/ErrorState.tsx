// Ошибка загрузки — что случилось человеческим языком + кнопка «Повторить»
// (spec 2026-08-31 §7). Раньше ошибка сети тихо превращалась в пустой список
// (`.catch(() => setItems([]))`) — оператор не отличал «пусто» от «не загрузилось».

export function ErrorState({
  message = "Не получилось загрузить данные.",
  onRetry,
}: {
  message?: string;
  onRetry: () => void;
}) {
  return (
    <div className="adm-error" role="alert">
      <p className="adm-error__text">{message}</p>
      <button className="btn" type="button" onClick={onRetry}>
        Повторить
      </button>
    </div>
  );
}
