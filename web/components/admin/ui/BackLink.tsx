export function BackLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="adm-back" type="button" onClick={onClick}>
      {label}
    </button>
  );
}
