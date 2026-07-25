import type { Metadata } from "next";

import { BriefForm, type BriefRow } from "@/components/BriefForm";

export const metadata: Metadata = {
  title: "Бриф для бизнеса — VK-Ads-auto",
};

// Порядок строк = порядок web/static/brief-community.html и, через него,
// COMMUNITY_FIELDS в services/brief_fields.py. Менять только синхронно с ним.
const ROWS: BriefRow[] = [
  { kind: "section", title: "Сообщество и контакт" },
  {
    kind: "input",
    num: "01",
    name: "full_name",
    label: "Контактное лицо *",
    autoComplete: "name",
    error: "Укажите имя",
  },
  {
    kind: "input",
    num: "02",
    name: "object_url",
    label: "Ссылка на сообщество VK *",
    placeholder: "https://vk.com/club...",
    error: "Укажите ссылку на сообщество",
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

  { kind: "section", title: "О бизнесе" },
  {
    kind: "input",
    num: "07",
    name: "niche",
    label: "Ниша / сфера *",
    placeholder: "например, кофейня, автосервис",
    error: "Укажите нишу",
  },
  {
    kind: "select",
    num: "08",
    name: "org_type",
    label: "Форма организации *",
    error: "Выберите форму организации",
    options: [
      { value: "", label: "—" },
      { value: "ИП", label: "ИП" },
      { value: "ООО / юрлицо", label: "ООО / юрлицо" },
      { value: "Самозанятый", label: "Самозанятый" },
      { value: "Физлицо", label: "Физлицо" },
    ],
  },
  {
    kind: "textarea",
    num: "09",
    name: "product_description",
    label: "Что продвигаем (товар/услуга) *",
    error: "Опишите продукт или услугу",
  },
  {
    kind: "input",
    num: "10",
    name: "site_url",
    label: "Сайт (если есть)",
    placeholder: "https://",
  },
  {
    kind: "textarea",
    num: "11",
    name: "usp",
    label: "Чем вы лучше конкурентов",
  },

  { kind: "section", title: "Аудитория" },
  {
    kind: "textarea",
    num: "12",
    name: "audience_description",
    label: "Кого хотим привлекать *",
    error: "Опишите аудиторию",
  },
  {
    kind: "input",
    num: "13",
    name: "geo",
    label: "География *",
    placeholder: "Город, регион",
    error: "Укажите географию",
  },
  {
    kind: "select",
    num: "14",
    name: "gender",
    label: "Пол аудитории",
    options: [
      { value: "", label: "Любой" },
      { value: "мужской", label: "Мужской" },
      { value: "женский", label: "Женский" },
    ],
  },

  { kind: "section", title: "Бюджет" },
  {
    kind: "input",
    num: "15",
    name: "budget",
    label: "Бюджет на рекламу *",
    placeholder: "например, 50000 ₽ или «готов обсудить»",
    error: "Укажите бюджет",
  },
  {
    kind: "input",
    num: "16",
    name: "term",
    label: "Срок / период *",
    placeholder: "например, 1 месяц",
    error: "Укажите срок",
  },
];

export default function BriefCommunity() {
  return (
    <>
      <header className="topbar">
        <div className="brand">
          VK<span>·</span>Ads<span>·</span>auto
        </div>
      </header>

      <main className="wrap">
        <div className="eyebrow">Бриф · подписчики в сообщество</div>
        <h2>Расскажите о компании и продвижении</h2>
        <p className="lead">Поля со звёздочкой обязательны. Это займёт пару минут.</p>

        <BriefForm variant="community" rows={ROWS} />
      </main>
    </>
  );
}
