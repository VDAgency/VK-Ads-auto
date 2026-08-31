// Пустой список — не «нет данных», а что это значит и что делать дальше
// (spec 2026-08-31 §7).

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="adm-empty">
      <p className="adm-empty__title">{title}</p>
      {description ? <p className="adm-empty__desc">{description}</p> : null}
      {action ? (
        <button className="btn" type="button" onClick={action.onClick}>
          {action.label}
        </button>
      ) : null}
    </div>
  );
}
