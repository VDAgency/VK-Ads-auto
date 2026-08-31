"use client";

// Мастер запуска кампании: шаги ровно как в боте — кабинет → цель → креатив →
// подтверждение (spec 2026-08-31-admin-cabinet-design-brief.md §5). «Цель» и
// «Креатив» пропускаются для площадок, где креатив не нужен
// (`card.surface_needs_creative === false`) — запуск идёт через
// `POST /briefs/{id}/launch` без них, ровно как решает бот
// (`bot/handlers/brief_card.py::launch_without_creative`).
//
// Запуск живёт только на последнем шаге (Т3) — этот компонент не содержит
// никакого другого пути к `POST .../launch` или `.../creative`.

import { useEffect, useRef, useState } from "react";

import {
  adminFetch,
  STATUS_RU,
  type AdAccount,
  type BriefCard,
  type Flash,
  type LaunchGoal,
  type LaunchOutcome,
} from "@/lib/adminApi";
import { routeHash } from "@/lib/adminRoute";

import { CabinetStep } from "./CabinetStep";
import { ConfirmStep } from "./ConfirmStep";
import { CreativeStep } from "./CreativeStep";
import { FieldsStep } from "./FieldsStep";
import { GoalStep } from "./GoalStep";
import { StepTrack } from "./StepTrack";
import { STEP_TITLES, type PickedFile, type WizardStepId } from "./types";

export function LaunchWizard({
  brief_id,
  card,
  onCardUpdate,
  onFlash,
  onBack,
}: {
  brief_id: number;
  card: BriefCard;
  onCardUpdate: (card: BriefCard) => void;
  onFlash: (flash: Flash) => void;
  /** К списку брифов — используется на экране успеха и на экране «кампания
   * уже есть», чтобы не запускать вторую случайно (см. `confirmedRelaunch`). */
  onBack: () => void;
}) {
  const needsCreative = card.surface_needs_creative !== false;
  const steps: WizardStepId[] = needsCreative
    ? ["fields", "cabinet", "goal", "creative", "confirm"]
    : ["fields", "cabinet", "confirm"];

  const [currentStep, setCurrentStep] = useState<WizardStepId>("fields");
  const [account, setAccount] = useState<AdAccount | null>(null);
  const [goalCode, setGoalCode] = useState<string | null>(null);
  const [goalTitle, setGoalTitle] = useState<string | null>(null);
  const [picked, setPicked] = useState<PickedFile | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  // Найденный баг: повторное нажатие «Запустить» после успеха создавало вторую
  // кампанию по тому же брифу — кнопка оставалась на экране и снова активной.
  // `justLaunched` заменяет весь шаг подтверждения на итог: кнопки «Запустить»
  // после успеха больше нет вовсе, а не просто заблокирована.
  const [justLaunched, setJustLaunched] = useState<LaunchOutcome | null>(null);

  // Открыли бриф, по которому кампания уже есть (`card.campaign_status` от
  // ядра — своей логики «можно ли запускать» здесь не изобретаем): по
  // умолчанию мастер это показывает, а не молча предлагает пройти шаги заново.
  // Запуск ещё одной кампании возможен только через отдельное явное действие.
  const [confirmedRelaunch, setConfirmedRelaunch] = useState(false);
  const existingCampaignStatus = card.campaign_status;
  const alreadyLaunched = Boolean(existingCampaignStatus) && !justLaunched && !confirmedRelaunch;

  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, [currentStep]);

  function goToNext(fromStep: WizardStepId) {
    const index = steps.indexOf(fromStep);
    const next = steps[index + 1];
    if (next) setCurrentStep(next);
  }

  async function refreshCard() {
    try {
      onCardUpdate(await adminFetch<BriefCard>(`/briefs/${brief_id}`));
    } catch {
      // Карточка обновится при следующей ручной перезагрузке — запуск уже
      // отработал и сообщение об этом оператор увидел, тихий сбой обновления
      // статуса не стоит превращать в ложную тревогу об ошибке запуска.
    }
  }

  const summaries: Partial<Record<WizardStepId, string>> = {
    cabinet: account ? account.title : undefined,
    goal: goalTitle ?? undefined,
    creative: picked ? picked.file.name : undefined,
  };

  const currentIndex = steps.indexOf(currentStep);

  if (justLaunched) {
    return (
      <div className="adm-panel" role="status">
        <p className="wiz-offer__title">Кампания запущена</p>
        <p>{justLaunched.message}</p>
        <div className="adm-actions">
          <button className="btn btn--primary" type="button" onClick={onBack}>
            К списку брифов
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => {
              window.location.hash = routeHash.campaigns();
            }}
          >
            Открыть кампании
          </button>
        </div>
      </div>
    );
  }

  if (alreadyLaunched) {
    return (
      <div className="adm-panel">
        <p className="wiz-offer__title">По этому брифу уже есть кампания</p>
        <p>
          Статус:{" "}
          {existingCampaignStatus
            ? (STATUS_RU[existingCampaignStatus] ?? existingCampaignStatus)
            : ""}
          . Повторный запуск создаст ещё одну кампанию по тому же брифу и спишет деньги клиента ещё
          раз.
        </p>
        <div className="adm-actions">
          <button
            className="btn btn--primary"
            type="button"
            onClick={() => {
              window.location.hash = routeHash.campaigns();
            }}
          >
            Открыть кампании
          </button>
          <button className="btn" type="button" onClick={onBack}>
            К списку брифов
          </button>
          <button
            className="btn btn--ghost"
            type="button"
            onClick={() => {
              const confirmed = window.confirm(
                "Точно запустить ещё одну кампанию по этому брифу? Прошлая кампания останется " +
                  "как есть, деньги спишутся ещё раз.",
              );
              if (confirmed) setConfirmedRelaunch(true);
            }}
          >
            Всё равно запустить ещё одну
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      <span className="visually-hidden" aria-live="polite">
        {`Шаг ${currentIndex + 1} из ${steps.length}: ${STEP_TITLES[currentStep]}`}
      </span>

      <StepTrack
        steps={steps}
        current={currentStep}
        summaries={summaries}
        onJump={setCurrentStep}
        headingRef={headingRef}
      >
        {currentStep === "fields" ? (
          <FieldsStep
            brief_id={brief_id}
            card={card}
            onCardUpdate={onCardUpdate}
            onFlash={onFlash}
            onContinue={() => goToNext("fields")}
          />
        ) : null}

        {currentStep === "cabinet" ? (
          <CabinetStep
            brief_id={brief_id}
            card={card}
            onCardUpdate={onCardUpdate}
            onFlash={onFlash}
            onPicked={(picked_account) => {
              setAccount(picked_account);
              goToNext("cabinet");
            }}
          />
        ) : null}

        {currentStep === "goal" ? (
          <GoalStep
            onPicked={(goal: LaunchGoal) => {
              setGoalCode(goal.code);
              setGoalTitle(goal.title);
              goToNext("goal");
            }}
          />
        ) : null}

        {currentStep === "creative" ? (
          <CreativeStep
            picked={picked}
            onPickedChange={setPicked}
            title={title}
            onTitleChange={setTitle}
            body={body}
            onBodyChange={setBody}
            onContinue={() => goToNext("creative")}
          />
        ) : null}

        {currentStep === "confirm" && account ? (
          <ConfirmStep
            brief_id={brief_id}
            account={account}
            needsCreative={needsCreative}
            goalCode={goalCode}
            goalTitle={goalTitle}
            picked={picked}
            title={title}
            body={body}
            onFlash={onFlash}
            onChangeCabinet={() => setCurrentStep("cabinet")}
            onLaunched={(outcome) => {
              setJustLaunched(outcome);
              onFlash({ text: outcome.message, ok: true, persistent: true });
              void refreshCard();
            }}
          />
        ) : null}
        {currentStep === "confirm" && !account ? (
          // Защитный случай: до подтверждения дошли, не выбрав кабинет
          // (например, из-за ручного перехода по истории) — возвращаем на
          // шаг кабинета вместо падения без данных для сводки.
          <p className="note">
            Кабинет ещё не выбран.{" "}
            <button className="btn" type="button" onClick={() => setCurrentStep("cabinet")}>
              Выбрать кабинет
            </button>
          </p>
        ) : null}
      </StepTrack>
    </>
  );
}
