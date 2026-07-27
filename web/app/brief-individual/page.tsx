import type { Metadata } from "next";
import Link from "next/link";

import { BriefForm, type BriefRow } from "@/components/BriefForm";
import { BRIEF_SURFACES } from "@/lib/briefSurfaces";

import "../brief.css";

export const metadata: Metadata = {
  title: "Бриф для физлица — VK-Ads-auto",
};

const BUDGET_OPTIONS = [
  { value: "5 000 ₽", label: "5 000 ₽" },
  { value: "10 000 ₽", label: "10 000 ₽" },
  { value: "15 000 ₽", label: "15 000 ₽" },
  { value: "20 000 ₽", label: "20 000 ₽" },
  { value: "25 000 ₽", label: "25 000 ₽" },
  { value: "30 000 ₽", label: "30 000 ₽" },
  { value: "до 50 000 ₽", label: "до 50 000 ₽" },
  { value: "готов обсудить", label: "Готов(а) обсудить" },
];

// Площадки подписки — единый список для обеих форм брифа (web/lib/briefSurfaces.ts).
const SURFACE_OPTIONS = BRIEF_SURFACES.map((surface) => ({
  value: surface.value,
  label: surface.label,
  disabled: !surface.enabled,
}));

const TERM_OPTIONS = [
  { value: "1 неделя", label: "1 неделя" },
  { value: "2 недели", label: "2 недели" },
  { value: "1 месяц", label: "1 месяц" },
  { value: "нужна консультация", label: "Нужна консультация" },
];

// Значения материалов разбирает `parse_materials` в services/brief_parser.py
// по ключевым словам «фото» / «видео» / «ничего нет» — менять только вместе с ним.
const MATERIALS_OPTIONS = [
  { value: "есть фото", label: "Да, есть фото" },
  { value: "есть видео", label: "Да, есть видео" },
  { value: "есть фото и видео", label: "Есть и фото, и видео" },
  { value: "ничего нет, нужна помощь", label: "Ничего нет, нужна помощь" },
];

// Порядок строк = порядок INDIVIDUAL_FIELDS в services/brief_fields.py.
// Менять только синхронно с ним: по этим номерам оператор правит сводку в боте.
const ROWS: BriefRow[] = [
  { kind: "section", num: 1, title: "Контактная информация" },
  {
    kind: "input",
    name: "full_name",
    maxLength: 120,
    label: "Как к вам обращаться",
    placeholder: "Фамилия Имя Отчество",
    autoComplete: "name",
    required: true,
    error: "Укажите имя",
  },
  {
    kind: "pair",
    items: [
      {
        kind: "input",
        name: "phone",
        maxLength: 24,
        label: "Телефон / WhatsApp",
        type: "tel",
        inputMode: "tel",
        placeholder: "+7 (___) ___-__-__",
        autoComplete: "tel",
        required: true,
        error: "Укажите телефон",
      },
      {
        kind: "input",
        name: "telegram",
        maxLength: 64,
        label: "Telegram",
        placeholder: "@username",
      },
    ],
  },
  {
    kind: "input",
    name: "email",
    maxLength: 254,
    label: "E-mail",
    type: "email",
    placeholder: "your@email.ru",
    autoComplete: "email",
    required: true,
    error: "Укажите email",
  },
  {
    kind: "input",
    name: "tax_id",
    maxLength: 12,
    pattern: "[0-9]{10,12}",
    label: "ИНН",
    hint: "Если есть, укажите — понадобится для документов",
    placeholder: "Например: 7707083893",
    inputMode: "numeric",
  },

  { kind: "section", num: 2, title: "Ваша страница ВКонтакте" },
  {
    kind: "input",
    name: "object_url",
    maxLength: 300,
    label: "Ссылка на личную страницу или сообщество ВК",
    hint: "Скопируйте ссылку из адресной строки браузера или из приложения",
    type: "url",
    placeholder: "https://vk.com/your_page",
    required: true,
    error: "Укажите ссылку на страницу",
  },
  {
    kind: "instruction",
    title: "Как скопировать ссылку на свою страницу ВК",
    steps: [
      <>
        Откройте приложение ВКонтакте или зайдите на <strong>vk.com</strong>
      </>,
      <>Перейдите в свой профиль (нажмите на аватарку или «Моя страница»)</>,
      <>
        В приложении: нажмите <strong>⋯</strong> (три точки) → <strong>«Копировать ссылку»</strong>
      </>,
      <>
        В браузере: скопируйте адрес из строки сверху (начинается с <strong>vk.com/</strong>)
      </>,
      <>Вставьте ссылку в поле выше</>,
    ],
  },
  {
    kind: "input",
    name: "vk_ad_cabinet_id",
    maxLength: 12,
    pattern: "[0-9]{4,12}",
    label: "ID кабинета VK Реклама",
    hint: "Номер вашего рекламного кабинета. Без него запустить кампанию не получится",
    inputMode: "numeric",
    placeholder: "например, 13410929",
    required: true,
    error: "Укажите ID кабинета VK Реклама",
    link: {
      href: "/instrukciya-vk-cabinet.html",
      text: "Как создать кабинет и найти ID — инструкция",
    },
  },
  {
    kind: "choices",
    name: "target_type",
    label: "Куда привлекаем подписчиков?",
    required: true,
    error: "Выберите вариант",
    options: SURFACE_OPTIONS,
  },

  { kind: "section", num: 3, title: "Кого хотите привлечь" },
  {
    kind: "textarea",
    name: "audience_description",
    maxLength: 1000,
    label: "Кто ваш идеальный подписчик?",
    hint: "Опишите, каких людей вы хотите видеть в подписчиках",
    placeholder: "Например: девушки 20–35, интересуются модой и красотой, живут в крупных городах…",
    required: true,
    error: "Опишите аудиторию",
  },
  // Пол и возраст — отдельными строками, а не парой: три карточки пола не
  // помещаются в половину ширины и переносятся, оставляя рваную пустоту.
  {
    kind: "choices",
    name: "gender",
    label: "Пол",
    options: [
      { value: "мужской", label: "Мужской" },
      { value: "женский", label: "Женский" },
      { value: "любой", label: "Любой" },
    ],
  },
  { kind: "age", label: "Возраст", hint: "Оставьте пустым, если возраст неважен" },
  {
    kind: "input",
    name: "geo",
    maxLength: 300,
    label: "География",
    hint: "Из каких городов или регионов нужны подписчики? Если неважно, напишите «вся Россия»",
    placeholder: "Москва, СПб / вся Россия",
    required: true,
    error: "Укажите географию",
  },

  { kind: "section", num: 4, title: "Бюджет и сроки" },
  {
    kind: "select",
    name: "budget",
    label: "Рекламный бюджет",
    hint: "Сумма, которую вы готовы вложить в рекламу (без учёта оплаты за настройку)",
    placeholder: "Выберите бюджет",
    required: true,
    error: "Укажите бюджет",
    options: BUDGET_OPTIONS,
  },
  {
    kind: "select",
    name: "term",
    label: "На какой срок планируете?",
    placeholder: "Выберите срок",
    required: true,
    error: "Укажите срок",
    options: TERM_OPTIONS,
  },

  { kind: "section", num: 5, title: "Рекламные материалы" },
  {
    kind: "choices",
    name: "materials",
    label: "Есть ли у вас фото или видео для рекламы?",
    options: MATERIALS_OPTIONS,
  },
  {
    kind: "input",
    name: "materials_url",
    maxLength: 300,
    label: "Ссылка на рекламные материалы",
    hint: "Пришлите ссылку на папку с фото и видео: Google Диск, Яндекс.Диск, облако",
    type: "url",
    placeholder: "https://disk.yandex.ru/...",
  },

  { kind: "section", num: 6, title: "Дополнительно" },
  {
    kind: "textarea",
    name: "competitors",
    maxLength: 1000,
    label: "Ссылки на конкурентов или примеры",
    hint: "Если есть кто-то, на кого вы хотите быть похожи, пришлите ссылки",
    rows: 3,
    placeholder: "https://vk.com/example1\nhttps://vk.com/example2",
  },
  {
    kind: "textarea",
    name: "extra",
    maxLength: 2000,
    label: "Есть ли что-то ещё, что важно учесть?",
    rows: 3,
    placeholder: "Любая дополнительная информация",
  },
];

export default function BriefIndividual() {
  return (
    <div className="bf">
      <a className="bf-skip" href="#bf-main">
        Перейти к форме
      </a>

      <header className="bf-header">
        <div className="bf-header__inner">
          <Link className="bf-brand" href="/">
            Ads<span className="bf-brand__dot">·</span>auto
          </Link>
          <Link className="bf-header__back" href="/">
            На главную
          </Link>
        </div>
      </header>

      <main id="bf-main">
        <div className="bf-hero">
          <div className="bf-hero__badge">Бриф</div>
          <h1 className="bf-hero__title">Подписчики на вашу страницу или сообщество ВКонтакте</h1>
          <p className="bf-hero__sub">
            Ответьте на вопросы о продвижении — по ним мы соберём кампанию. Займёт около десяти
            минут. Поля со звёздочкой обязательны, остальное можно пропустить.
          </p>
        </div>

        <div className="bf-container">
          <BriefForm
            variant="individual"
            rows={ROWS}
            footer={
              <>
                После отправки откроется ваш <strong>личный кабинет</strong> — там будет статус
                брифа и запуск кампании
              </>
            }
          />
        </div>
      </main>
    </div>
  );
}
