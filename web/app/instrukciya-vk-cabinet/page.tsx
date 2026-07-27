import type { Metadata } from "next";

import { BackLink } from "@/components/BackLink";

import "./instrukciya.css";

export const metadata: Metadata = {
  title: "Как создать кабинет VK Реклама и найти ID — инструкция",
};

export default function InstrukciyaVkCabinet() {
  return (
    <>
      <a className="skip-link" href="#ins-main">
        Перейти к инструкции
      </a>
      <div className="topbar">
        <div className="brand">
          Ads<span>·</span>auto
        </div>
      </div>

      <main className="wrap" id="ins-main">
        <div className="eyebrow">Инструкция</div>
        <h1>Как создать кабинет VK Реклама и узнать его ID</h1>
        <p className="lead">
          Чтобы мы запустили рекламу за вас, нужен ваш собственный рекламный кабинет VK Реклама.
          Создайте его по шагам ниже и укажите <b>ID кабинета</b> в брифе — этого достаточно, дальше
          запуск и статистику берём на себя.
        </p>

        <div className="callout">
          <b>Коротко:</b> зайдите на ads.vk.ru → войдите через почту → создайте новый кабинет (тип
          «Рекламодатель», физическое или юридическое лицо, валюта — рубль) → скопируйте{" "}
          <b>ID кабинета</b> из настроек → вставьте его в поле брифа «ID кабинета VK Реклама».
        </div>

        <div className="steps">
          <div className="step">
            <h2>Откройте сайт VK Реклама</h2>
            <p>
              Перейдите на <code>https://ads.vk.ru/</code> в браузере компьютера.
            </p>
            <p className="note">Регистрировать кабинет удобнее с компьютера, а не с телефона.</p>
          </div>

          <div className="step">
            <h2>Если вы уже где-то авторизованы — выйдите</h2>
            <p>
              Если в правом верхнем углу видно чужой профиль или другой аккаунт — нажмите «Выйти».
              Так вы точно создадите кабинет на нужный аккаунт.
            </p>
          </div>

          <div className="step">
            <h2>Нажмите «Перейти в кабинет»</h2>
            <p>Кнопка входа в кабинет — в правом верхнем углу страницы.</p>
          </div>

          <div className="step">
            <h2>Войдите через почту</h2>
            <p>
              В окне VK ID выберите вход <b>через почту</b> (Mail). Важно: в этом браузере вы должны
              быть залогинены именно в той почте, которую хотите привязать к рекламному кабинету.
            </p>
            <div className="mock">
              <div className="mock__bar">
                <b>VK ID</b> · Вход в VK Реклама
              </div>
              <div className="mock__body">
                <div className="radio">
                  <span className="dot"></span> Телефон
                </div>
                <div className="radio on">
                  <span className="dot"></span> Почта{" "}
                  <span className="tag">выберите этот способ</span>
                </div>
              </div>
            </div>
          </div>

          <div className="step">
            <h2>Создайте новый кабинет</h2>
            <p>
              Если кабинета ещё нет — VK Реклама предложит его создать. Нажмите «Создать кабинет».
            </p>
          </div>

          <div className="step">
            <h2>Выберите тип аккаунта</h2>
            <p>На экране «Регистрация кабинета» укажите:</p>
            <div className="mock">
              <div className="mock__bar">Регистрация кабинета</div>
              <div className="mock__body">
                <div className="sidewords">Тип аккаунта</div>
                <div className="radio on">
                  <span className="dot"></span> Рекламодатель <span className="tag">выбрать</span>
                </div>
                <div className="radio">
                  <span className="dot"></span> Агентство
                </div>
                <div className="field-hint">
                  Страна — <b>Россия</b>, валюта — <b>Российский рубль (RUB)</b>.
                </div>
                <div className="sidewords" style={{ marginTop: 14 }}>
                  Тип лица
                </div>
                <div className="radio on">
                  <span className="dot"></span> Физическое лицо <span className="tag">или</span>
                </div>
                <div className="radio">
                  <span className="dot"></span> Юридическое лицо
                </div>
              </div>
            </div>
            <p className="note">
              Выберите «Физическое лицо», если реклама от вас лично, или «Юридическое лицо» для
              ИП/компании. Затем подтвердите создание кабинета.
            </p>
          </div>

          <div className="step">
            <h2>Скопируйте ID кабинета и вставьте в бриф</h2>
            <p>
              Откройте в кабинете раздел <b>«Настройки»</b> (внизу левого меню) → вкладка{" "}
              <b>«Общие»</b>. В самом верху блока показан <b>ID кабинета</b> — скопируйте это число.
            </p>
            <div className="mock">
              <div className="mock__bar">Настройки · Общие</div>
              <div className="mock__body">
                <div className="kv">
                  <div className="k">ID кабинета</div>
                  <div className="v mono">1461&nbsp;•&nbsp;•&nbsp;•&nbsp;•</div>
                  <div className="k">Тип кабинета</div>
                  <div className="v">Рекламодатель / Клиент агентства</div>
                </div>
                <div className="field-hint">Ровно это число и нужно вставить в бриф.</div>
              </div>
            </div>
            <p>
              Вставьте скопированный ID в поле брифа <b>«ID кабинета VK Реклама»</b> — и отправьте
              бриф. Всё остальное сделает автоматизация.
            </p>
          </div>
        </div>

        <div className="callout">
          Не получается создать кабинет или найти ID? Напишите нам — поможем и подскажем.
        </div>

        <BackLink className="backlink">← Вернуться к брифу</BackLink>
      </main>
    </>
  );
}
