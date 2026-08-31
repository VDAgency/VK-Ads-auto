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
      <div className="section-title">Рекламные кабинеты</div>
      <AdAccounts />

      <div className="section-title">Каналы доставки</div>
      <DeliveryChannels />

      <div className="section-title">Senler</div>
      <SenlerSection />

      <div className="section-title">Площадки размещения</div>
      <SurfacesReference />
    </>
  );
}
