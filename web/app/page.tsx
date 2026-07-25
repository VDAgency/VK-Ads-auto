import type { Metadata } from "next";
import Link from "next/link";

import { LoginModal } from "@/components/LoginModal";
import { ScrollReveal } from "@/components/ScrollReveal";

// Стили лендинга. Импорт в самой странице — Next привяжет CSS-чанк к этому
// маршруту, поэтому остальные страницы его не тянут.
import "./landing.css";

export const metadata: Metadata = {
  title: "VK-Ads-auto — запуск рекламы в VK из брифа за минуты",
  description:
    "Независимый сервис для запуска рекламы в VK Ads: короткий бриф, авто-раскладка в настройки кампании, запуск и отслеживание. Быстрее и легче, чем вручную.",
};

// Прогрессивное улучшение. `js` на <html> включает скрытие reveal-блоков (без JS
// контент остаётся видимым), `lp` на <body> — локальную палитру лендинга из
// landing.css. Скрипт синхронный и стоит первым в разметке, поэтому классы
// проставляются до отрисовки контента и мигания не будет.
const BOOTSTRAP_CLASSES = `document.documentElement.classList.add("js");document.body.classList.add("lp");`;

/** Галочка в списке доверия под геро-блоком. */
function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 6 9 17l-5-5"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Стрелка между шагами «как это работает». */
function StepArrow() {
  return (
    <svg
      className="lp-step__arrow"
      width="38"
      height="38"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M5 12h14M13 6l6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Brand() {
  return (
    <Link className="lp-brand" href="/" aria-label="VK-Ads-auto — на главную">
      <span className="lp-badge" aria-hidden="true">
        ВК
      </span>
      <span>
        Ads<span className="lp-brand__dot">·</span>auto
      </span>
    </Link>
  );
}

export default function Landing() {
  return (
    <>
      <script dangerouslySetInnerHTML={{ __html: BOOTSTRAP_CLASSES }} />

      {/* ================= Header ================= */}
      <header className="lp-header">
        <div className="lp-container lp-header__inner">
          <Brand />
          <nav className="lp-nav" aria-label="Основная навигация">
            <a className="lp-nav__link" href="#how">
              Как это работает
            </a>
            <a className="lp-nav__link" href="#features">
              Возможности
            </a>
            <a className="lp-nav__link" href="/cabinet.html" data-open-login>
              Вход в кабинет
            </a>
            <a className="lp-nav__cta" href="#start">
              Заполнить бриф
            </a>
          </nav>
        </div>
      </header>

      <main>
        {/* ================= Hero ================= */}
        <section className="lp-hero">
          <div className="lp-hero__bg" aria-hidden="true">
            <span className="lp-blob lp-blob--1"></span>
            <span className="lp-blob lp-blob--2"></span>
            <span className="lp-blob lp-blob--3"></span>
            <div className="lp-grid"></div>
          </div>

          <div className="lp-container lp-hero__inner">
            <div className="lp-hero__copy">
              <span className="lp-eyebrow reveal">
                <span className="lp-eyebrow__dot" aria-hidden="true"></span>
                Реклама в VK без рутины
              </span>

              <h1 className="lp-h1 reveal reveal--d1">
                От брифа до запущенной кампании в&nbsp;VK — <span className="lp-hl">за минуты</span>
                , а не часы.
              </h1>

              <p className="lp-lead reveal reveal--d2">
                Клиент заполняет короткий бриф, сервис раскладывает его в настройки кампании,
                оператор подтверждает — и реклама уходит в VK&nbsp;Ads. Быстрее и легче, чем
                собирать кампанию в кабинете вручную.
              </p>

              <div className="lp-cta-row reveal reveal--d3">
                <a className="lp-btn lp-btn--light" href="/brief-individual.html">
                  <span className="lp-btn__label">
                    Заполнить бриф
                    <small>Физлицо · личная страница</small>
                  </span>
                </a>
                <a className="lp-btn lp-btn--glass" href="/brief-community.html">
                  <span className="lp-btn__label">
                    Заполнить бриф
                    <small>Бизнес · сообщество</small>
                  </span>
                </a>
              </div>

              <p className="lp-hero__login reveal reveal--d3">
                Уже отправляли бриф?{" "}
                <a href="/cabinet.html" data-open-login>
                  Войти в личный кабинет
                </a>
              </p>

              <ul className="lp-trust reveal reveal--d3">
                <li>
                  <CheckIcon />
                  Бриф за пару минут
                </li>
                <li>
                  <CheckIcon />
                  Настройки собираются автоматически
                </li>
                <li>
                  <CheckIcon />
                  Запуск под ключ
                </li>
              </ul>
            </div>

            {/* Анимированная схема: бриф → авто-раскладка → запущенная кампания. */}
            <div className="lp-hero__art reveal reveal--d2">
              <svg
                className="lp-art"
                viewBox="0 0 520 440"
                role="img"
                aria-label="Схема: бриф превращается в запущенную кампанию VK со статистикой"
              >
                <title>Бриф превращается в запущенную кампанию</title>

                {/* Потоки между узлами */}
                <path className="lp-flow" d="M182 244 H252" />
                <path className="lp-flow" d="M320 240 C 362 240, 382 250, 424 250" />

                {/* Карточка брифа */}
                <g>
                  <rect className="lp-art__card" x="30" y="150" width="152" height="192" rx="18" />
                  <rect className="lp-art__accent" x="50" y="172" width="70" height="14" rx="7" />
                  <rect className="lp-art__line" x="50" y="204" width="112" height="8" rx="4" />
                  <rect
                    className="lp-art__line lp-art__line--muted"
                    x="50"
                    y="224"
                    width="94"
                    height="8"
                    rx="4"
                  />
                  <rect
                    className="lp-art__line lp-art__line--muted"
                    x="50"
                    y="244"
                    width="102"
                    height="8"
                    rx="4"
                  />
                  <rect
                    className="lp-art__line lp-art__line--muted"
                    x="50"
                    y="264"
                    width="70"
                    height="8"
                    rx="4"
                  />
                  <circle cx="60" cy="304" r="11" fill="#12a594" />
                  <path
                    d="M55 304 l3.5 3.5 L65 300"
                    fill="none"
                    stroke="#fff"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <rect x="80" y="300" width="72" height="8" rx="4" fill="#dbe6f5" />
                </g>

                {/* Узел авто-раскладки */}
                <g>
                  <circle className="lp-ring" cx="286" cy="242" r="34" />
                  <circle className="lp-ring lp-ring--2" cx="286" cy="242" r="34" />
                  <circle className="lp-ring-rot" cx="286" cy="242" r="30" />
                  <circle
                    cx="286"
                    cy="242"
                    r="24"
                    fill="rgba(255,255,255,0.14)"
                    stroke="rgba(255,255,255,0.55)"
                    strokeWidth="1.5"
                  />
                  <rect x="276" y="232" width="8" height="8" rx="2" fill="#ffffff" />
                  <rect x="288" y="232" width="8" height="8" rx="2" fill="#7dffbf" />
                  <rect x="276" y="244" width="8" height="8" rx="2" fill="#7dffbf" />
                  <rect x="288" y="244" width="8" height="8" rx="2" fill="#ffffff" />
                </g>

                {/* Ракета: запущенная кампания */}
                <g className="lp-rocket">
                  <path d="M430 214 L414 252 L430 240 Z" fill="#cfe0ff" />
                  <path d="M466 214 L482 252 L466 240 Z" fill="#cfe0ff" />
                  <rect x="430" y="150" width="36" height="98" rx="18" fill="#ffffff" />
                  <path d="M430 168 a18 18 0 0 1 36 0 Z" fill="#0077ff" />
                  <circle cx="448" cy="192" r="10" fill="#0062da" />
                  <circle cx="448" cy="192" r="10" fill="none" stroke="#7dffbf" strokeWidth="2" />
                  <rect x="430" y="224" width="36" height="9" fill="#cfe0ff" />
                  <path
                    className="lp-flame"
                    d="M438 248 q10 30 20 0 q-4 12 -10 15 q-6 -3 -10 -15 Z"
                  />
                </g>

                {/* Столбики статистики: отслеживание */}
                <g>
                  <line
                    x1="404"
                    y1="412"
                    x2="502"
                    y2="412"
                    stroke="rgba(255,255,255,0.4)"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                  <rect className="lp-bar" x="412" y="374" width="18" height="38" rx="4" />
                  <rect
                    className="lp-bar lp-bar--2"
                    x="440"
                    y="360"
                    width="18"
                    height="52"
                    rx="4"
                  />
                  <rect
                    className="lp-bar lp-bar--3"
                    x="468"
                    y="386"
                    width="18"
                    height="26"
                    rx="4"
                  />
                </g>

                {/* Искры */}
                <circle className="lp-spark" cx="360" cy="150" r="4" />
                <circle className="lp-spark lp-spark--2" cx="214" cy="112" r="3" />
                <circle className="lp-spark lp-spark--3" cx="486" cy="128" r="3.5" />
              </svg>
            </div>
          </div>
        </section>

        {/* ================= Как это работает ================= */}
        <section className="lp-section" id="how">
          <div className="lp-container">
            <div className="lp-section__head reveal">
              <span className="lp-kicker">Как это работает</span>
              <h2 className="lp-h2">Три шага от идеи до открутки</h2>
              <p className="lp-sub">
                Никаких таблиц с настройками и ручного переноса в кабинет — сервис проходит путь от
                заявки до запуска за вас.
              </p>
            </div>

            <ol className="lp-steps">
              <li className="lp-step reveal">
                <span className="lp-step__num">1</span>
                <h3>Бриф за пару минут</h3>
                <p>
                  Клиент открывает короткую форму и отвечает на понятные вопросы о продвижении.
                  Ничего лишнего.
                </p>
                <StepArrow />
              </li>
              <li className="lp-step reveal reveal--d1">
                <span className="lp-step__num">2</span>
                <h3>Авто-раскладка</h3>
                <p>
                  Ответы автоматически превращаются в структуру кампании: цель, аудитория, формат и
                  настройки для VK&nbsp;Ads.
                </p>
                <StepArrow />
              </li>
              <li className="lp-step reveal reveal--d2">
                <span className="lp-step__num">3</span>
                <h3>Запуск и отслеживание</h3>
                <p>
                  Оператор подтверждает — кампания уходит в VK&nbsp;Ads. Статус и результат видно в
                  личном кабинете.
                </p>
              </li>
            </ol>
          </div>
        </section>

        {/* ================= Возможности ================= */}
        <section className="lp-section lp-section--tint" id="features">
          <div className="lp-container">
            <div className="lp-section__head reveal">
              <span className="lp-kicker">Почему так удобнее</span>
              <h2 className="lp-h2">Быстрее, легче и под ключ</h2>
              <p className="lp-sub">
                Мы убрали ручную рутину запуска, чтобы реклама стартовала без задержек, а вы
                занимались результатом.
              </p>
            </div>

            <div className="lp-features">
              <article className="lp-feature reveal">
                <span className="lp-feature__icon" aria-hidden="true">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <h3>Быстрее</h3>
                <p>
                  Кампания собирается из брифа за <span className="lp-feature__accent">минуты</span>
                  , а не за часы ручной настройки.
                </p>
              </article>

              <article className="lp-feature reveal reveal--d1">
                <span className="lp-feature__icon" aria-hidden="true">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <h3>Легче</h3>
                <p>
                  Клиенту — простая форма вместо длинных ТЗ. Оператору —{" "}
                  <span className="lp-feature__accent">готовая раскладка</span> вместо ручного
                  ввода.
                </p>
              </article>

              <article className="lp-feature reveal reveal--d2">
                <span className="lp-feature__icon" aria-hidden="true">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M9 12l2 2 4-4"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <path
                      d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6l7-3Z"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <h3>Под ключ</h3>
                <p>
                  От заявки до запуска в VK&nbsp;Ads —{" "}
                  <span className="lp-feature__accent">весь путь</span> в одном сервисе, без
                  переключений между инструментами.
                </p>
              </article>

              <article className="lp-feature reveal reveal--d3">
                <span className="lp-feature__icon" aria-hidden="true">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M4 19V5m0 14h16M8 15l3-4 3 2 4-6"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                <h3>Прозрачно</h3>
                <p>
                  Статус брифа и запущенной кампании —{" "}
                  <span className="lp-feature__accent">в личном кабинете</span>, понятно клиенту.
                </p>
              </article>
            </div>
          </div>
        </section>

        {/* ================= Финальный CTA ================= */}
        <section className="lp-cta" id="start">
          <div className="lp-container">
            <div className="lp-cta__card reveal">
              <div className="lp-cta__glow" aria-hidden="true"></div>
              <h2>Запустим рекламу в VK из вашего брифа</h2>
              <p>
                Выберите форму по типу продвижения и заполните бриф — остальное сервис возьмёт на
                себя.
              </p>
              <div className="lp-cta__row">
                <a className="lp-btn lp-btn--light" href="/brief-individual.html">
                  <span className="lp-btn__label">
                    Бриф для физлица
                    <small>Личная страница VK</small>
                  </span>
                </a>
                <a className="lp-btn lp-btn--glass" href="/brief-community.html">
                  <span className="lp-btn__label">
                    Бриф для бизнеса
                    <small>Сообщество ИП / ООО</small>
                  </span>
                </a>
              </div>
              <p className="lp-cta__note">
                Обычно ссылку на нужный бриф присылает таргетолог. Если вы попали сюда сами —
                выберите форму по типу продвижения. Уже отправляли бриф?{" "}
                <a href="/cabinet.html" data-open-login>
                  Войти в личный кабинет
                </a>
                .
              </p>
            </div>
          </div>
        </section>
      </main>

      {/* ================= Footer ================= */}
      <footer className="lp-footer">
        <div className="lp-container lp-footer__inner">
          <Brand />
          <nav className="lp-footer__links" aria-label="Ссылки в подвале">
            <a href="/brief-individual.html">Бриф · физлицо</a>
            <a href="/brief-community.html">Бриф · бизнес</a>
            <a href="#how">Как это работает</a>
          </nav>
        </div>
        <div className="lp-container lp-footer__copy">
          Независимый сервис автоматизации запуска рекламы в VK&nbsp;Ads. Не является продуктом VK и
          не аффилирован с VK.
        </div>
      </footer>

      <LoginModal />
      <ScrollReveal />
    </>
  );
}
