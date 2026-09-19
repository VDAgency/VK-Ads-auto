"use client";

import { useEffect, useRef, useState } from "react";

import { readDraft } from "@/lib/briefDraft";
import {
  BRIEF_GOAL_TABS,
  DEFAULT_GOAL_TAB,
  isGoalTabEnabled,
  surfacesForTab,
  tabForSurfaceValue,
} from "@/lib/briefGoals";

import type { BriefVariant } from "./BriefForm";

type GoalKey = (typeof BRIEF_GOAL_TABS)[number]["key"];

type Props = {
  variant: BriefVariant;
  /** Бизнес-бриф несёт отдельное декоративное поле `goal` (services/brief_fields.py);
   * у брифа физлица его в канонической карте нет — добавлять нельзя (сдвинет
   * нумерацию правок `номер.значение`). */
  includeGoalField: boolean;
  invalid: ReadonlySet<string>;
};

/**
 * Блок «цель рекламы → площадка → ссылка на объект».
 *
 * Раньше это были три независимых вопроса (два для физлица), и половина из
 * 15 площадок противоречила заголовку «Куда привлекаем подписчиков?» — лид-форма
 * не привлекает подписчиков, сообщения ведут в диалог. Теперь клиент сначала
 * выбирает цель вкладкой (`BRIEF_GOAL_TABS`, `web/lib/briefGoals.ts`), а
 * площадки внутри неё отфильтрованы по цели (`web/lib/briefSurfaces.ts`,
 * поле `goal` каждой площадки зеркалит `integrations/vk_surfaces.py`).
 *
 * Панели ВСЕХ вкладок смонтированы одновременно (не по одной): у панели
 * неактивной вкладки атрибуты `hidden` и `disabled` на `<fieldset>` — второй
 * не декоративный, а физически исключает её радио из FormData при отправке
 * (нативное поведение диалогов `disabled` для вложенных полей формы), даже
 * если пользователь когда-то отметил там вариант, а потом переключился на
 * другую вкладку. Так восстановление черновика (`BriefForm`) может отмечать
 * галочкой любую площадку сразу при монтировании, не дожидаясь переключения
 * на нужную вкладку — а выбор на каждой вкладке не теряется при уходе с неё
 * и возврате назад.
 */
export function BriefGoalSurface({ variant, includeGoalField, invalid }: Props) {
  const [activeGoal, setActiveGoal] = useState<GoalKey>(DEFAULT_GOAL_TAB.key);
  const tabRefs = useRef<Partial<Record<GoalKey, HTMLButtonElement | null>>>({});

  // Переключить видимую вкладку на ту, к которой относится сохранённое
  // значение target_type — сами радио восстанавливает общий механизм в
  // `BriefForm` (они всегда смонтированы, вне зависимости от вкладки, см.
  // комментарий у `BriefGoalSurface` выше), этот эффект только синхронизирует
  // ВИДИМУЮ вкладку с внешним хранилищем (localStorage) при первом монтировании
  // — источник состояния снаружи React, для этого эффекты и предназначены.
  // Без чтения `localStorage` на клиенте (после монтирования) серверный и
  // первый клиентский рендер разошлись бы (там `window` недоступен вовсе,
  // см. `readDraft`) — считать состояние вкладки во время рендера нельзя,
  // поэтому обычный «lazy useState» здесь не подходит.
  useEffect(() => {
    const draftValue = readDraft(variant).target_type;
    if (!draftValue) return;
    const tab = tabForSurfaceValue(draftValue);
    if (!tab || !isGoalTabEnabled(tab)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- см. комментарий выше
    setActiveGoal(tab.key);
  }, [variant]);

  function handleTabsKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const enabled = BRIEF_GOAL_TABS.filter((tab) => isGoalTabEnabled(tab));
    const currentIndex = enabled.findIndex((tab) => tab.key === activeGoal);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % enabled.length;
    else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + enabled.length) % enabled.length;
    } else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = enabled.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = enabled[nextIndex];
    setActiveGoal(next.key);
    tabRefs.current[next.key]?.focus();
  }

  const activeTab = BRIEF_GOAL_TABS.find((tab) => tab.key === activeGoal) ?? DEFAULT_GOAL_TAB;
  const targetTypeInvalid = invalid.has("target_type");
  const objectUrlInvalid = invalid.has("object_url");

  return (
    <>
      <div className="bf-field">
        <span className="bf-field__label" id="goal-tabs-label">
          Цель рекламы
        </span>
        <span className="bf-hint">
          Выберите, чего хотите добиться рекламой — площадки ниже зависят от цели
        </span>
        <div
          className="bf-goal-tabs"
          role="tablist"
          aria-labelledby="goal-tabs-label"
          onKeyDown={handleTabsKeyDown}
        >
          {BRIEF_GOAL_TABS.map((tab) => {
            const isActive = tab.key === activeGoal;
            const tabEnabled = isGoalTabEnabled(tab);
            return (
              <button
                key={tab.key}
                ref={(el) => {
                  tabRefs.current[tab.key] = el;
                }}
                type="button"
                role="tab"
                id={`goal-tab-${tab.key}`}
                aria-selected={isActive}
                aria-controls={`goal-panel-${tab.key}`}
                tabIndex={tabEnabled ? (isActive ? 0 : -1) : undefined}
                disabled={!tabEnabled}
                className={isActive ? "bf-goal-tab is-active" : "bf-goal-tab"}
                onClick={() => setActiveGoal(tab.key)}
              >
                {tab.label}
                {!tabEnabled ? <span className="bf-choice__soon">скоро</span> : null}
              </button>
            );
          })}
        </div>
      </div>

      {BRIEF_GOAL_TABS.map((tab) => {
        const surfaces = surfacesForTab(tab);
        // Единственная площадка цели — подставляем сама, выбирать не из чего
        // (требование §4 спеки). Если единственная площадка ещё не проверена в
        // бою, оставляем обычную (заблокированную) карточку выбора.
        const soleSurface = surfaces.length === 1 && surfaces[0].enabled ? surfaces[0] : null;
        const isActive = tab.key === activeGoal;
        return (
          <fieldset
            key={tab.key}
            id={`goal-panel-${tab.key}`}
            className="bf-goal-panel"
            role="tabpanel"
            aria-labelledby={`goal-tab-${tab.key}`}
            hidden={!isActive}
            disabled={!isActive}
          >
            {soleSurface ? (
              <>
                <input type="hidden" name="target_type" value={soleSurface.value} readOnly />
                <p className="bf-goal-auto">
                  Площадка: <strong>{soleSurface.label}</strong> — единственный вариант для этой
                  цели, подставляется автоматически.
                </p>
              </>
            ) : (
              <div className="bf-field">
                <span className="bf-field__label" id={`target_type-label-${tab.key}`}>
                  Площадка
                  <span className="bf-req" aria-hidden="true">
                    {" "}
                    *
                  </span>
                </span>
                <div
                  className="bf-choices"
                  role="radiogroup"
                  aria-labelledby={`target_type-label-${tab.key}`}
                  aria-describedby={isActive && targetTypeInvalid ? "target_type-error" : undefined}
                >
                  {surfaces.map((surface, index) => {
                    const id = `target_type-${tab.key}-${index}`;
                    return (
                      <div className="bf-choice" key={surface.value}>
                        <input
                          type="radio"
                          id={id}
                          name="target_type"
                          value={surface.value}
                          disabled={!surface.enabled}
                        />
                        <label htmlFor={id}>
                          <span className="bf-choice__mark" aria-hidden="true" />
                          <span className="bf-choice__body">
                            <span>{surface.label}</span>
                            {surface.note ? (
                              <span className="bf-choice__note">{surface.note}</span>
                            ) : null}
                          </span>
                          {!surface.enabled ? <span className="bf-choice__soon">скоро</span> : null}
                        </label>
                      </div>
                    );
                  })}
                </div>
                {isActive && targetTypeInvalid ? (
                  <div className="bf-field__error" id="target_type-error">
                    Выберите площадку
                  </div>
                ) : null}
              </div>
            )}
          </fieldset>
        );
      })}

      {includeGoalField ? (
        <input type="hidden" name="goal" value={activeTab.label} readOnly />
      ) : null}

      <div className={objectUrlInvalid ? "bf-field is-invalid" : "bf-field"}>
        <label className="bf-field__label" htmlFor="object_url">
          {activeTab.objectUrl.label}
          <span className="bf-req" aria-hidden="true">
            {" "}
            *
          </span>
        </label>
        <span className="bf-hint">{activeTab.objectUrl.hint}</span>
        <input
          id="object_url"
          name="object_url"
          type="url"
          maxLength={300}
          placeholder={activeTab.objectUrl.placeholder}
          required
          aria-invalid={objectUrlInvalid || undefined}
          aria-describedby={objectUrlInvalid ? "object_url-error" : undefined}
        />
        {objectUrlInvalid ? (
          <div className="bf-field__error" id="object_url-error">
            Укажите ссылку на объект
          </div>
        ) : null}
      </div>

      {variant === "individual" && activeGoal === "subscription" ? (
        <div className="bf-instruction">
          <div className="bf-instruction__title">Как скопировать ссылку на свою страницу ВК</div>
          <ol>
            <li>
              Откройте приложение ВКонтакте или зайдите на <strong>vk.com</strong>
            </li>
            <li>Перейдите в свой профиль (нажмите на аватарку или «Моя страница»)</li>
            <li>
              В приложении: нажмите <strong>⋯</strong> (три точки) →{" "}
              <strong>«Копировать ссылку»</strong>
            </li>
            <li>
              В браузере: скопируйте адрес из строки сверху (начинается с <strong>vk.com/</strong>)
            </li>
            <li>Вставьте ссылку в поле выше</li>
          </ol>
        </div>
      ) : null}
    </>
  );
}
