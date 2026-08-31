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
      <div className="section-title">Кампании</div>
      <CampaignList />

      <div className="section-title">Статистика по кабинетам</div>
      <CabinetStats />
    </>
  );
}
