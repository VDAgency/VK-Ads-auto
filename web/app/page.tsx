import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

import { LoginModal } from "@/components/LoginModal";
import { ScrollReveal } from "@/components/ScrollReveal";

import "./landing.css";

export const metadata: Metadata = {
  title: "Запуск рекламы ВКонтакте из короткого брифа — Ads·auto",
  description:
    "Вы заполняете бриф, сервис собирает из него настройки кампании, таргетолог проверяет и запускает рекламу ВКонтакте. Статус и результат видно в личном кабинете.",
};

/**
 * Блок «кто ведёт» появится, когда будут получены материалы и разрешение на
 * публикацию: имя, фото, опыт, кейсы с цифрами. До тех пор секция не
 * рендерится — выдумывать доказательства нельзя, а заглушка с правдоподобным
 * текстом читалась бы как настоящая.
 */
type Author = {
  name: string;
  role: string;
  photo: string;
  experience: string;
  quote: string;
};

const AUTHOR: Author | null = null;

function BriefCardArt() {
  return (
    <div className="lp-art" aria-hidden="true">
      <div className="lp-art__card lp-art__card--brief">
        <span className="lp-art__label">Бриф</span>
        <ul className="lp-art__rows">
          <li>
            <span className="lp-art__n">01</span>
            <span className="lp-art__bar" style={{ inlineSize: "62%" }} />
          </li>
          <li>
            <span className="lp-art__n">02</span>
            <span className="lp-art__bar" style={{ inlineSize: "84%" }} />
          </li>
          <li>
            <span className="lp-art__n">03</span>
            <span className="lp-art__bar" style={{ inlineSize: "48%" }} />
          </li>
          <li>
            <span className="lp-art__n">04</span>
            <span className="lp-art__bar" style={{ inlineSize: "72%" }} />
          </li>
        </ul>
      </div>

      <div className="lp-art__link">
        <svg viewBox="0 0 24 24" fill="none" width="20" height="20">
          <path
            d="M5 12h14M13 6l6 6-6 6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>

      <div className="lp-art__card lp-art__card--campaign">
        <span className="lp-art__label">Кампания</span>
        <dl className="lp-art__specs">
          <div>
            <dt>Цель</dt>
            <dd>Подписчики</dd>
          </div>
          <div>
            <dt>Аудитория</dt>
            <dd>Ж, 25–45, Москва</dd>
          </div>
          <div>
            <dt>Бюджет</dt>
            <dd className="lp-art__mono">20 000 ₽</dd>
          </div>
        </dl>
        <span className="badge badge--accent lp-art__status">Запущена</span>
      </div>
    </div>
  );
}

const STEPS = [
  {
    title: "Вы заполняете бриф",
    text: "Понятные вопросы о том, кого и куда вы хотите привлечь. Без терминов рекламного кабинета: отвечаете своими словами.",
  },
  {
    title: "Сервис собирает кампанию",
    text: "Ответы автоматически превращаются в настройки: цель, аудитория, география, бюджет и сроки. Вручную ничего переносить не нужно.",
  },
  {
    title: "Таргетолог проверяет и запускает",
    text: "Собранную кампанию смотрит живой специалист, при необходимости правит и подтверждает запуск. Дальше статус виден в личном кабинете.",
  },
];

const FAQ = [
  {
    q: "Что нужно, чтобы начать?",
    a: "Сообщество или страница ВКонтакте, которую вы продвигаете, рекламный кабинет VK Реклама и заполненный бриф. Если кабинета ещё нет, мы подготовили пошаговую инструкцию, как его создать и где взять номер.",
  },
  {
    q: "Нужен ли мне свой рекламный кабинет VK?",
    a: "Да. Реклама запускается в вашем кабинете и с вашего бюджета — так вы остаётесь владельцем аккаунта и статистики. Создание кабинета занимает несколько минут и делается один раз.",
  },
  {
    q: "Какие цели рекламы доступны сейчас?",
    a: "Сейчас доступна одна цель — подписчики в сообщество или на личную страницу. Сообщения, лид-формы и заявки через Senler появятся позже, в брифе они отмечены как недоступные.",
  },
  {
    q: "Что происходит после отправки брифа?",
    a: "Бриф попадает к таргетологу, сервис собирает из него черновик кампании. Вы получаете доступ в личный кабинет, где видно, на каком этапе находится работа.",
  },
  {
    q: "Можно ли изменить что-то после отправки?",
    a: "Да. Правки вносятся до запуска: напишите, что поменять, и кампания будет пересобрана. Каждое поле брифа пронумеровано, поэтому достаточно назвать номер и новое значение.",
  },
  {
    q: "Кто отвечает за результат?",
    a: "Кампанию собирает автоматика, но решение о запуске принимает практикующий таргетолог. Автоматизация убирает ручную работу и ускоряет старт, а за настройками стоит человек.",
  },
  {
    q: "Где я вижу результат?",
    a: "В личном кабинете: статус брифа, состояние кампании и результат по цели. Вход по паролю или по ссылке на почту.",
  },
];

export default function Landing() {
  return (
    <>
      <a className="skip-link" href="#main">
        Перейти к содержимому
      </a>

      <header className="lp-header">
        <div className="lp-container lp-header__inner">
          <Link className="lp-brand" href="/">
            Ads<span className="lp-brand__dot">·</span>auto
          </Link>
          <nav className="lp-nav" aria-label="Основная навигация">
            <a className="lp-nav__link" href="#how">
              Как это работает
            </a>
            <a className="lp-nav__link" href="#faq">
              Вопросы
            </a>
            <a className="lp-nav__login" href="/cabinet.html" data-open-login>
              Вход
            </a>
            <a className="btn btn--primary lp-nav__cta" href="#start">
              Заполнить бриф
            </a>
          </nav>
        </div>
      </header>

      <main id="main">
        <section className="lp-hero">
          <div className="lp-container lp-hero__inner">
            <div className="lp-hero__copy">
              <span className="lp-eyebrow">Реклама ВКонтакте</span>
              <h1 className="lp-h1">Бриф вместо технического задания. Запуск вместо переписки.</h1>
              <p className="lp-lead">
                Вы отвечаете на понятные вопросы о своём продвижении. Сервис собирает из ответов
                настройки кампании, таргетолог проверяет их и запускает рекламу ВКонтакте.
              </p>

              <div className="lp-cta-row">
                <a className="btn btn--primary lp-cta" href="/brief-individual.html">
                  <span>
                    Заполнить бриф
                    <small>Личная страница или сообщество</small>
                  </span>
                </a>
                <a className="btn lp-cta" href="/brief-community.html">
                  <span>
                    Заполнить бриф
                    <small>Бизнес: ИП, ООО, самозанятый</small>
                  </span>
                </a>
              </div>

              <p className="lp-hero__login">
                Уже отправляли бриф?{" "}
                <a href="/cabinet.html" data-open-login>
                  Войти в личный кабинет
                </a>
              </p>
            </div>

            <div className="lp-hero__art">
              <BriefCardArt />
            </div>
          </div>
        </section>

        <section className="lp-section" id="how">
          <div className="lp-container">
            <div className="lp-head reveal">
              <span className="lp-kicker">Как это работает</span>
              <h2>Три шага от брифа до запущенной рекламы</h2>
              <p className="lp-sub">
                Ручной перенос настроек в рекламный кабинет убирается целиком. Остаётся то, что
                действительно требует человека: проверка и решение о запуске.
              </p>
            </div>

            <ol className="lp-steps">
              {STEPS.map((step, index) => (
                <li className="lp-step reveal" key={step.title}>
                  <span className="lp-step__num">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <h3>{step.title}</h3>
                    <p>{step.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="lp-section lp-section--sunk">
          <div className="lp-container">
            <div className="lp-head reveal">
              <h2>Меньше рутины на каждой стороне</h2>
              <p className="lp-sub">
                Ниже — что именно снимается с вас и с таргетолога, когда бриф становится источником
                настроек.
              </p>
            </div>

            <div className="lp-bento">
              <article className="lp-tile lp-tile--wide reveal">
                <h3>Вам не нужно разбираться в рекламном кабинете</h3>
                <p>
                  Бриф написан обычным языком. Никаких групп объявлений, плейсментов и look-alike:
                  вы описываете, кого хотите привлечь, остальное переводит сервис.
                </p>
              </article>

              <article className="lp-tile reveal">
                <h3>Настройки собираются сами</h3>
                <p>Цель, аудитория, география, бюджет и сроки выводятся из ответов брифа.</p>
              </article>

              <article className="lp-tile reveal">
                <h3>Правки по номерам</h3>
                <p>
                  Каждое поле пронумеровано. Чтобы что-то изменить, достаточно назвать номер и новое
                  значение.
                </p>
              </article>

              <article className="lp-tile lp-tile--wide reveal">
                <h3>Статус виден вам, а не только исполнителю</h3>
                <p>
                  В личном кабинете видно, на каком этапе бриф и что с кампанией. Не нужно писать и
                  спрашивать, как дела.
                </p>
              </article>
            </div>
          </div>
        </section>

        {AUTHOR ? (
          <section className="lp-section" id="author">
            <div className="lp-container lp-author reveal">
              <Image
                className="lp-author__photo"
                src={AUTHOR.photo}
                alt=""
                width={160}
                height={160}
              />
              <div>
                <span className="lp-kicker">Кто ведёт</span>
                <h2>{AUTHOR.name}</h2>
                <p className="lp-author__role">{AUTHOR.role}</p>
                <p>{AUTHOR.experience}</p>
                <blockquote className="lp-author__quote">{AUTHOR.quote}</blockquote>
              </div>
            </div>
          </section>
        ) : null}

        <section className="lp-section" id="faq">
          <div className="lp-container lp-container--narrow">
            <div className="lp-head reveal">
              <h2>Что обычно спрашивают</h2>
            </div>

            <div className="lp-faq reveal">
              {FAQ.map((item) => (
                <details className="lp-faq__item" key={item.q}>
                  <summary>
                    <span>{item.q}</span>
                    <svg
                      className="lp-faq__chevron"
                      viewBox="0 0 24 24"
                      width="20"
                      height="20"
                      fill="none"
                      aria-hidden="true"
                    >
                      <path
                        d="m6 9 6 6 6-6"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final" id="start">
          <div className="lp-container">
            <div className="lp-final__card reveal">
              <h2>Выберите бриф по типу продвижения</h2>
              <p>
                Заполнение занимает около десяти минут: понадобится ссылка на страницу или
                сообщество и номер рекламного кабинета VK.
              </p>
              <div className="lp-final__row">
                <a className="btn btn--primary lp-cta" href="/brief-individual.html">
                  <span>
                    Бриф для частного лица
                    <small>Личная страница или сообщество</small>
                  </span>
                </a>
                <a className="btn lp-cta" href="/brief-community.html">
                  <span>
                    Бриф для бизнеса
                    <small>ИП, ООО, самозанятый</small>
                  </span>
                </a>
              </div>
              <p className="lp-final__note">
                Нет рекламного кабинета?{" "}
                <a href="/instrukciya-vk-cabinet.html">Инструкция, как его создать</a>.
              </p>
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-container lp-footer__inner">
          <Link className="lp-brand lp-brand--footer" href="/">
            Ads<span className="lp-brand__dot">·</span>auto
          </Link>
          <nav className="lp-footer__links" aria-label="Ссылки в подвале">
            <a href="/brief-individual.html">Бриф: частное лицо</a>
            <a href="/brief-community.html">Бриф: бизнес</a>
            <a href="/instrukciya-vk-cabinet.html">Как создать кабинет VK</a>
            <a href="#faq">Вопросы</a>
          </nav>
        </div>
        <div className="lp-container lp-footer__legal">
          Независимый сервис запуска рекламы. Не является продуктом VK и не аффилирован с VK.
        </div>
      </footer>

      <LoginModal />
      <ScrollReveal />
    </>
  );
}
