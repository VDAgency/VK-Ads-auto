import { AdAccounts } from "../AdAccounts";
import { DeliveryChannels } from "../channels/DeliveryChannels";
import { SenlerSection } from "../channels/SenlerSection";
import { SurfacesReference } from "../channels/SurfacesReference";

/**
 * «Кабинеты и каналы» (spec 2026-08-31-admin-cabinet-design-brief.md, задача 2):
 * рекламные кабинеты VK + состояние каналов доставки (юзербот/kotbot, только
 * просмотр) + привязка Senler + справочник площадок размещения. Каждый блок —
 * свой компонент с собственным состоянием загрузки/ошибки: раздел большой,
 * дробить на файлы обязательно (не держать всё в одном месте).
 */
export function ChannelsScreen() {
  return (
    <>
      <h2 className="section-title">Рекламные кабинеты</h2>
      <AdAccounts />

      <h2 className="section-title">Каналы доставки</h2>
      <DeliveryChannels />

      <h2 className="section-title">Senler</h2>
      <SenlerSection />

      <h2 className="section-title">Площадки размещения</h2>
      <SurfacesReference />
    </>
  );
}
