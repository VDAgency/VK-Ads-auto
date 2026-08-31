import { AdAccounts } from "../AdAccounts";

/**
 * «Кабинеты и каналы»: рекламные кабинеты VK — сегодня единственный готовый
 * раздел. Состояние каналов доставки (Senler) и справочник площадок размещения
 * — задел на следующую задачу (эндпоинты `admin_channels.py`/`admin_operations.py`
 * в ядре уже готовы, экрана под них пока нет — см. отчёт по каркасу).
 */
export function ChannelsScreen() {
  return (
    <>
      <div className="section-title">Рекламные кабинеты</div>
      <AdAccounts />
    </>
  );
}
