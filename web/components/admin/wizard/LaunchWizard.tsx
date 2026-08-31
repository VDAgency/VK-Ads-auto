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
  type AdAccount,
  type BriefCard,
  type Flash,
  type LaunchGoal,
} from "@/lib/adminApi";

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
}: {
  brief_id: number;
  card: BriefCard;
  onCardUpdate: (card: BriefCard) => void;
  onFlash: (flash: Flash) => void;
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
            onLaunched={(message) => {
              onFlash({ text: message, ok: true, persistent: true });
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
