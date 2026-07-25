import type { Metadata } from "next";
import Link from "next/link";

import { BriefForm, type BriefRow } from "@/components/BriefForm";
import { BRIEF_GOALS } from "@/lib/briefGoals";

import "../brief.css";

export const metadata: Metadata = {
  title: "Бриф для бизнеса — VK-Ads-auto",
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

const TERM_OPTIONS = [
  { value: "1 неделя", label: "1 неделя" },
  { value: "2 недели", label: "2 недели" },
  { value: "1 месяц", label: "1 месяц" },
  { value: "нужна консультация", label: "Нужна консультация" },
];

// Значения материалов разбирает `parse_materials` в services/brief_parser.py
// по ключевым словам «фото» / «видео» / «ничего нет» — менять только вместе с ним.
const MATERIALS_OPTIONS = [
  { value: "есть фото", label: "📸 Да, есть фото" },
  { value: "есть видео", label: "🎬 Да, есть видео" },
  { value: "есть фото и видео", label: "📸🎬 Есть и фото, и видео" },
  { value: "ничего нет, нужна помощь", label: "❌ Ничего нет, нужна помощь" },
];

// Значения разбирает `parse_org_type` в services/brief_parser.py.
const ORG_TYPE_OPTIONS = [
  { value: "физлицо", label: "Физ. лицо" },
  { value: "самозанятый", label: "Самозанятый" },
  { value: "ИП", label: "ИП" },
  { value: "ООО / юрлицо", label: "ООО / Юр. лицо" },
  { value: "иностранное юрлицо", label: "Иностранное юр. лицо" },
  { value: "иностранное физлицо", label: "Иностранное физ. лицо" },
];

// Доступность целей — единственный флаг в web/lib/briefGoals.ts.
const GOAL_OPTIONS = BRIEF_GOALS.map((goal) => ({
  value: goal.value,
  label: goal.label,
  disabled: !goal.enabled,
}));

// Порядок строк = порядок COMMUNITY_FIELDS в services/brief_fields.py.
// Менять только синхронно с ним: по этим номерам оператор правит сводку в боте.
const ROWS: BriefRow[] = [
  { kind: "section", num: 1, title: "Контактная информация" },
  {
    kind: "pair",
    items: [
      {
        kind: "input",
        name: "full_name",
        label: "ФИО",
        placeholder: "Фамилия Имя Отчество",
        autoComplete: "name",
        required: true,
        error: "Укажите имя",
      },
      {
        kind: "input",
        name: "company",
        label: "Компания / проект",
        placeholder: "Название бизнеса",
      },
    ],
  },
  {
    kind: "pair",
    items: [
      {
        kind: "input",
        name: "phone",
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
        label: "Telegram",
        placeholder: "@username",
      },
    ],
  },
  {
    kind: "input",
    name: "email",
    label: "E-mail",
    type: "email",
    placeholder: "your@email.ru",
    autoComplete: "email",
    required: true,
    error: "Укажите email",
  },
  {
    kind: "input",
    name: "niche",
    label: "Ниша / сфера деятельности",
    placeholder: "Например: доставка еды, фитнес, интернет-магазин одежды…",
    required: true,
    error: "Укажите нишу",
  },
  {
    kind: "choices",
    name: "org_type",
    label: "Тип организации",
    required: true,
    error: "Выберите форму организации",
    options: ORG_TYPE_OPTIONS,
  },
  {
    kind: "input",
    name: "tax_id",
    label: "ИНН / ОГРН / ОГРНИП",
    hint: "Если есть — укажите для оформления рекламного кабинета и документов",
    placeholder: "Например: 7707083893",
    inputMode: "numeric",
  },
  {
    kind: "input",
    name: "org_name",
    label: "Наименование организации (для юр. лиц)",
    placeholder: "ООО «Название»",
  },

  { kind: "section", num: 2, title: "О продукте и услуге" },
  {
    kind: "input",
    name: "object_url",
    label: "Ссылка на сообщество ВКонтакте",
    hint: "Именно это сообщество мы будем продвигать",
    type: "url",
    placeholder: "https://vk.com/your_group",
    required: true,
    error: "Укажите ссылку на сообщество",
  },
  {
    kind: "input",
    name: "vk_ad_cabinet_id",
    label: "ID кабинета VK Реклама",
    hint: "Номер вашего рекламного кабинета — без него мы не сможем запустить кампанию",
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
    kind: "input",
    name: "site_url",
    label: "Ссылка на сайт (если есть)",
    type: "url",
    placeholder: "https://yoursite.ru",
  },
  {
    kind: "textarea",
    name: "product_description",
    label: "Краткое описание продукта или услуги",
    hint: "Что продаёте? В чём суть? Какую проблему решаете?",
    placeholder: "Опишите продукт: что это, для кого, какой результат получает клиент…",
    required: true,
    error: "Опишите продукт или услугу",
  },
  {
    kind: "input",
    name: "avg_check",
    label: "Средний чек / диапазон цен",
    placeholder: "Например: от 3 000 до 15 000 ₽",
  },
  {
    kind: "textarea",
    name: "usp",
    label: "Ваше УТП — чем вы лучше конкурентов",
    hint: "Почему клиенту стоит выбрать именно вас?",
    placeholder: "Бесплатная доставка, гарантия возврата, уникальная технология…",
  },
  {
    kind: "textarea",
    name: "offers",
    label: "Есть ли актуальные акции, скидки, спецпредложения?",
    rows: 3,
    placeholder: "Например: первый урок бесплатно, скидка 20% до конца месяца…",
  },

  { kind: "section", num: 3, title: "Целевая аудитория" },
  {
    kind: "textarea",
    name: "audience_description",
    label: "Кто ваш идеальный клиент?",
    hint: "Опишите типичного покупателя: кто он, чем занимается, какая у него боль?",
    placeholder: "Женщины 25–45, мамы в декрете, хотят подработку на дому…",
    required: true,
    error: "Опишите аудиторию",
  },
  {
    kind: "pair",
    items: [
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
      { kind: "age", label: "Возраст" },
    ],
  },
  {
    kind: "input",
    name: "geo",
    label: "География продвижения",
    hint: "Города, регионы, районы. Если вся Россия — так и напишите",
    placeholder: "Москва, МО, Санкт-Петербург / вся Россия",
    required: true,
    error: "Укажите географию",
  },
  {
    kind: "input",
    name: "exclusions",
    label: "Кому НЕ нужно показывать рекламу?",
    hint: "Исключения: конкуренты, нецелевые города, возрастные группы",
    placeholder: "Например: дети до 18, конкуренты…",
  },

  { kind: "section", num: 4, title: "Цель рекламы" },
  {
    kind: "choices",
    name: "goal",
    label: "Что хотите получить?",
    hint: "Сейчас мы запускаем кампании на подписчиков. Остальные цели помечены «скоро» — подключим их следующими.",
    options: GOAL_OPTIONS,
  },

  { kind: "section", num: 5, title: "Бюджет и сроки" },
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

  { kind: "section", num: 6, title: "Рекламные материалы" },
  {
    kind: "notice",
    text: "Рекламный материал должен соответствовать тому, что вы продаёте — иначе модерация VK его отклонит.",
  },
  {
    kind: "choices",
    name: "materials",
    label: "Есть ли у вас фото или видео для рекламы?",
    options: MATERIALS_OPTIONS,
  },
  {
    kind: "input",
    name: "materials_url",
    label: "Ссылка на рекламные материалы",
    hint: "Если есть — пришлите ссылку на папку с фото и видео (Google Диск, Яндекс.Диск, облако)",
    type: "url",
    placeholder: "https://disk.yandex.ru/...",
  },

  { kind: "section", num: 7, title: "Конкуренты" },
  {
    kind: "textarea",
    name: "competitors",
    label: "Ссылки на конкурентов (2–3 шт.)",
    hint: "Укажите тех, кто уже рекламируется или на кого вы хотите быть похожи",
    rows: 3,
    placeholder: "https://vk.com/competitor1\nhttps://vk.com/competitor2",
  },

  { kind: "section", num: 8, title: "Дополнительно" },
  {
    kind: "textarea",
    name: "extra",
    label: "Есть ли что-то ещё, что важно учесть?",
    hint: "Сезонность, ограничения, пожелания к креативам, особенности бизнеса…",
    rows: 4,
    placeholder: "Любая дополнительная информация, которая поможет в работе",
  },
];

export default function BriefCommunity() {
  return (
    <div className="bf">
      <header className="bf-header">
        <div className="bf-header__inner">
          <Link className="bf-brand" href="/">
            <span className="bf-brand__badge" aria-hidden="true">
              VK
            </span>
            VK<span className="bf-brand__dot">·</span>Ads
            <span className="bf-brand__dot">·</span>auto
          </Link>
          <Link className="bf-header__back" href="/">
            На главную
          </Link>
        </div>
      </header>

      <div className="bf-hero">
        <div className="bf-hero__badge">📋 Бриф</div>
        <h1 className="bf-hero__title">Запуск таргетированной рекламы VK для бизнеса</h1>
        <p className="bf-hero__sub">
          Заполните бриф, чтобы мы подготовили стратегию продвижения и запустили рекламу максимально
          эффективно. Поля со звёздочкой обязательны.
        </p>
      </div>

      <div className="bf-container">
        <BriefForm
          variant="community"
          rows={ROWS}
          footer={
            <>
              После отправки откроется ваш <strong>личный кабинет</strong>
              <br />— там будет статус брифа и запуск кампании
            </>
          }
        />
      </div>
    </div>
  );
}
