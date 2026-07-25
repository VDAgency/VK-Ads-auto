import type { Metadata } from "next";

import { BriefForm, type BriefRow } from "@/components/BriefForm";

export const metadata: Metadata = {
  title: "Бриф для физлица — VK-Ads-auto",
};

// Порядок строк = порядок web/static/brief-individual.html и, через него,
// INDIVIDUAL_FIELDS в services/brief_fields.py. Менять только синхронно с ним.
const ROWS: BriefRow[] = [
  { kind: "section", title: "О вас и объекте" },
  {
    kind: "input",
    num: "01",
    name: "full_name",
    label: "Как к вам обращаться *",
    autoComplete: "name",
    error: "Укажите имя",
  },
  {
    kind: "input",
    num: "02",
    name: "object_url",
    label: "Ссылка на вашу страницу VK *",
    placeholder: "https://vk.com/id...",
    error: "Укажите ссылку на страницу",
  },
  {
    kind: "input",
    num: "03",
    name: "vk_ad_cabinet_id",
    label: "ID кабинета VK Реклама *",
    inputMode: "numeric",
    placeholder: "например, 13410929",
    error: "Укажите ID кабинета VK Реклама",
    hint: {
      href: "/instrukciya-vk-cabinet.html",
      text: "Как создать кабинет и найти ID — инструкция",
    },
  },

  { kind: "section", title: "Контакты" },
  {
    kind: "input",
    num: "04",
    name: "email",
    label: "Email *",
    type: "email",
    autoComplete: "email",
    required: true,
    error: "Укажите email",
  },
  {
    kind: "input",
    num: "05",
    name: "phone",
    label: "Телефон *",
    inputMode: "tel",
    placeholder: "+7...",
    required: true,
    error: "Укажите телефон",
  },
  {
    kind: "input",
    num: "06",
    name: "telegram",
    label: "Telegram (если есть)",
    placeholder: "@username",
  },

  { kind: "section", title: "Аудитория" },
  {
    kind: "textarea",
    num: "07",
    name: "audience_description",
    label: "Кого хотим привлекать *",
    error: "Опишите аудиторию",
  },
  {
    kind: "input",
    num: "08",
    name: "geo",
    label: "География *",
    placeholder: "Город, регион",
    error: "Укажите географию",
  },
  {
    kind: "select",
    num: "09",
    name: "gender",
    label: "Пол аудитории",
    options: [
      { value: "", label: "Любой" },
      { value: "мужской", label: "Мужской" },
      { value: "женский", label: "Женский" },
    ],
  },
  { kind: "age", num: "10", label: "Возраст (от / до)" },

  { kind: "section", title: "Бюджет и цель" },
  {
    kind: "input",
    num: "11",
    name: "budget",
    label: "Бюджет на рекламу *",
    placeholder: "например, 30000 ₽ или «готов обсудить»",
    error: "Укажите бюджет",
  },
  {
    kind: "input",
    num: "12",
    name: "term",
    label: "Срок / период *",
    placeholder: "например, 1 месяц",
    error: "Укажите срок",
  },
  {
    kind: "select",
    num: "13",
    name: "target_type",
    label: "Куда привлекаем *",
    error: "Выберите вариант",
    options: [
      { value: "личная страница", label: "Личная страница" },
      { value: "сообщество", label: "Сообщество / группа" },
    ],
  },

  { kind: "section", title: "Материалы" },
  {
    kind: "select",
    num: "14",
    name: "materials",
    label: "Есть ли рекламные материалы",
    options: [
      { value: "", label: "—" },
      { value: "есть фото", label: "Есть фото" },
      { value: "есть видео", label: "Есть видео" },
      { value: "есть фото и видео", label: "Есть фото и видео" },
      { value: "ничего нет, нужна помощь", label: "Ничего нет, нужна помощь" },
    ],
  },
];

export default function BriefIndividual() {
  return (
    <>
      <header className="topbar">
        <div className="brand">
          VK<span>·</span>Ads<span>·</span>auto
        </div>
      </header>

      <main className="wrap">
        <div className="eyebrow">Бриф · продвижение личной страницы</div>
        <h2>Расскажите о продвижении</h2>
        <p className="lead">Поля со звёздочкой обязательны. Это займёт пару минут.</p>

        <BriefForm variant="individual" rows={ROWS} />
      </main>
    </>
  );
}
