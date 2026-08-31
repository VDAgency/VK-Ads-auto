import { CabinetStats } from "../campaigns/CabinetStats";
import { CampaignList } from "../campaigns/CampaignList";

/**
 * «Кампании»: паритет с `/stats` и `/stop_campaign` бота (spec 2026-08-31-
 * admin-cabinet-design-brief.md, задача 1). Два блока разнесены по своим
 * компонентам — список кампаний (цель, статус, остановка) и статистика по
 * кабинетам (синк, период, метрики) устроены и обновляются независимо.
 */
export function CampaignsScreen() {
  return (
    <>
      <h2 className="section-title">Кампании</h2>
      <CampaignList />

      <h2 className="section-title">Статистика по кабинетам</h2>
      <CabinetStats />
    </>
  );
}
