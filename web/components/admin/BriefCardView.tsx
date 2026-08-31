"use client";

// Карточка брифа — контейнер: загрузка данных + шапка (номер, статусы), сам
// мастер запуска — в `wizard/LaunchWizard.tsx` (spec 2026-08-31-admin-cabinet-
// design-brief.md §5). Файл был расписан на части по ответственности, а не
// правился одним растущим компонентом — правится и держится в голове проще.

import { useEffect, useState } from "react";

import { VARIANT_RU, type BriefCard, type Flash } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { StatusBadge } from "./ui/Badge";
import { BackLink } from "./ui/BackLink";
import { ErrorState } from "./ui/ErrorState";
import { SkeletonCard } from "./ui/Skeleton";
import { LaunchWizard } from "./wizard/LaunchWizard";

export function BriefCardView({
  id,
  onBack,
  onFlash,
}: {
  id: number;
  onBack: () => void;
  onFlash: (flash: Flash) => void;
}) {
  const [resource, retryResource] = useAdminResource<BriefCard>(`/briefs/${id}`, [id]);
  const [card, setCard] = useState<BriefCard | null>(null);

  // Держим карточку отдельным состоянием: после правок/запуска её обновляет
  // ответ мутации напрямую, не дожидаясь нового GET через хук.
  useEffect(() => {
    if (resource.status !== "ready") return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCard(resource.data);
  }, [resource]);

  if (resource.status === "loading" || !card) {
    return (
      <>
        <BackLink label="← к брифам" onClick={onBack} />
        <SkeletonCard />
      </>
    );
  }

  if (resource.status === "error") {
    return (
      <>
        <BackLink label="← к брифам" onClick={onBack} />
        <ErrorState message="Не удалось открыть бриф." onRetry={retryResource} />
      </>
    );
  }

  return (
    <>
      <BackLink label="← к брифам" onClick={onBack} />

      <div className="adm-card">
        <div className="adm-card__head">
          <h2>
            Бриф <span className="adm-mono">№{card.brief_id}</span>
          </h2>
          <span className="badge">{VARIANT_RU[card.variant] ?? card.variant}</span>
          <StatusBadge status={card.status} />
          <span className={card.has_creative ? "badge badge--accent" : "badge badge--warn"}>
            {card.has_creative ? "креатив загружен" : "креатива нет"}
          </span>
        </div>

        {/* `key` привязан к брифу: без него смена брифа по прямой навигации
            хэшем (минуя возврат к списку) могла бы оставить состояние мастера
            (выбранный кабинет, «кампания уже запущена» и т. п.) от прошлого
            брифа — риск недопустим именно здесь, где на кону деньги клиента. */}
        <LaunchWizard
          key={id}
          brief_id={id}
          card={card}
          onCardUpdate={setCard}
          onFlash={onFlash}
          onBack={onBack}
        />
      </div>
    </>
  );
}
