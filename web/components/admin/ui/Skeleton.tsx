// Заглушка на время загрузки: повторяет геометрию будущего контента, а не
// пустой экран и не спиннер по центру — макет не дёргается, когда данные
// приходят (spec 2026-08-31 §7: раньше загрузка рисовалась как `null`).
//
// Блоки пульсируют синхронно (не построчно — DESIGN.md §9 запрещает построчную
// анимацию списков) через `.adm-skel` в admin.css. При `prefers-reduced-motion`
// глобальное правило в styles.css обнуляет длительность — блок остаётся
// статичным плейсхолдером без движения.

function SkeletonBlock({ width = "100%", height = "1rem" }: { width?: string; height?: string }) {
  return <span className="adm-skel" style={{ width, height }} />;
}

/** Обёртка с объявлением для скринридера: сам скелет ему не нужен. */
function SkeletonRegion({ children }: { children: React.ReactNode }) {
  return (
    <div role="status">
      <span className="visually-hidden">Загрузка…</span>
      <div aria-hidden="true">{children}</div>
    </div>
  );
}

export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <SkeletonRegion>
      <div className="adm-list">
        {Array.from({ length: count }, (_, index) => (
          <div className="adm-row adm-row--skel" key={index}>
            <span className="adm-row__main">
              <SkeletonBlock width="55%" height="0.95rem" />
              <SkeletonBlock width="35%" height="0.75rem" />
            </span>
            <SkeletonBlock width="4.5rem" height="1.5rem" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function SkeletonTiles({ count = 4 }: { count?: number }) {
  return (
    <SkeletonRegion>
      <div className="adm-tiles">
        {Array.from({ length: count }, (_, index) => (
          <div className="adm-tile adm-tile--skel" key={index}>
            <SkeletonBlock width="2.5rem" height="1.75rem" />
            <SkeletonBlock width="70%" height="0.8rem" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function SkeletonCard() {
  return (
    <SkeletonRegion>
      <div className="adm-card">
        <SkeletonBlock width="40%" height="1.375rem" />
        <div style={{ marginTop: "0.75rem" }}>
          <SkeletonBlock width="65%" height="0.875rem" />
        </div>
        <div style={{ marginTop: "1.5rem", display: "grid", gap: "0.75rem" }}>
          <SkeletonBlock height="1.25rem" />
          <SkeletonBlock height="1.25rem" />
          <SkeletonBlock height="1.25rem" />
        </div>
      </div>
    </SkeletonRegion>
  );
}
