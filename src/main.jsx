import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowDownRight, ArrowRight, ArrowUpRight, AudioLines, BarChart3, ChevronDown, CircleDot, Command, Fingerprint, Layers3, Menu, Network, ShieldCheck, Sparkles, X } from 'lucide-react';
import './styles.css';

const valueStreams = [
  { icon: Network, number: '01', title: 'Доход растёт вместе с клиентом', text: 'Путь от личных финансов к самозанятости и ИП становится продолжением сервиса — только по инициативе человека.' },
  { icon: BarChart3, number: '02', title: 'Каждая задача стоит измеримо', text: 'Маршрутизация моделей, кэш и специализированные агенты снижают стоимость завершённой задачи.' },
  { icon: Layers3, number: '03', title: 'Инвестиции в AI работают сообща', text: 'Оркестрация связывает уже созданные модели и агентов вместо повторной сборки похожих сценариев.' },
];

const useCases = [
  { index: '01', title: 'Клиент замечает возможность', detail: 'При регулярных поступлениях помощник предлагает проверить подходящий статус — без обвинений и давления.' },
  { index: '02', title: 'Сравнивает варианты', detail: 'Самозанятость или ИП: понятное объяснение, налоговый календарь и нужные шаги на основе разрешённых данных.' },
  { index: '03', title: 'Переходит к делу по своему выбору', detail: 'При желании — оформление ИП, расчётный счёт и бизнес-сервисы. Передача данных требует отдельного подтверждения.' },
];

function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [model, setModel] = useState(null);

  useEffect(() => {
    fetch('/api/economics/')
      .then((response) => response.ok ? response.json() : Promise.reject(new Error('API unavailable')))
      .then(setModel)
      .catch(() => setModel(null));
  }, []);

  const closeMenu = () => setMenuOpen(false);

  return <>
    <header className="topbar">
      <a className="brand" href="#top" aria-label="СберОдин — на главную"><span className="brand-mark"><span /></span><span>Сбер<span className="brand-light">Один</span></span></a>
      <button className="menu-toggle" onClick={() => setMenuOpen(!menuOpen)} aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'}>{menuOpen ? <X /> : <Menu />}</button>
      <nav className={menuOpen ? 'nav nav-open' : 'nav'} aria-label="Основная навигация">
        <a href="#thesis" onClick={closeMenu}>Идея</a><a href="#model" onClick={closeMenu}>Экономика</a><a href="#pilot" onClick={closeMenu}>Сценарий</a><a href="#governance" onClick={closeMenu}>Доверие</a>
        <a className="nav-cta" href="#decision" onClick={closeMenu}>К предложению <ArrowUpRight size={16} /></a>
      </nav>
    </header>

    <main id="top">
      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow"><span className="pulse" /> Идея для экосистемы Сбера</div>
          <h1>Один помощник.<br /><span>Для разных ролей.</span></h1>
          <p className="hero-lede">84,7 млн человек ежемесячно пользуются СберБанк Онлайн; у Сбера уже есть GigaChat, бизнес-ассистент и более 1 000 внутренних AI-агентов. ЯдроСбер связывает эти активы в один путь: меньше повторов для клиента, больше завершённых задач и измеримый эффект для бизнеса — без объединения ролевых данных в одну базу.</p>
          <div className="hero-actions"><a className="button button-primary" href="#model">Посмотреть расчёты <ArrowRight size={17} /></a><a className="text-link" href="#pilot">Жизненный сценарий <ArrowDownRight size={16} /></a></div>
          <div className="hero-note"><ShieldCheck size={17} /><span>Решение о масштабе — после 90-дневного пилота и подтверждения эффекта против контрольной группы.</span></div>
        </div>
        <div className="hero-visual" aria-label="СберОдин направляет задачу к подходящему AI-инструменту">
          <div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit orbit-three" />
          <div className="core-card"><div className="core-icon"><Command size={25} /></div><span className="core-kicker">ОБЩИЙ СЛОЙ</span><strong>СберОдин</strong><span className="core-status"><i /> Выбирает помощника</span></div>
          <div className="node node-ai"><div className="node-icon"><Sparkles size={19} /></div><span>ГигаЧат</span></div>
          <div className="node node-bank"><div className="node-icon"><BarChart3 size={19} /></div><span>Giga-ассистент</span></div>
          <div className="node node-life"><div className="node-icon"><AudioLines size={19} /></div><span>Пульс</span></div>
          <div className="node node-insight"><div className="node-icon"><CircleDot size={19} /></div><span>AI-агенты</span></div>
          <div className="visual-label">ВОПРОС <span>→</span> НУЖНЫЙ ПОМОЩНИК</div>
        </div>
        <div className="hero-index"><span>КОНЦЕПЦИЯ ПРОДУКТА</span><span>ЛИСТАЙТЕ <ChevronDown size={14} /></span></div>
      </section>

      <section className="marquee" aria-label="Масштаб экосистемы"><span>110,7 МЛН ЧАСТНЫХ КЛИЕНТОВ</span><i /><span>84,7 МЛН MAU СБОЛ</span><i /><span>3,5 МЛН БИЗНЕС-КЛИЕНТОВ</span><i /><span>1 000+ ВНУТРЕННИХ AI-АГЕНТОВ</span></section>

      <section className="platform section-wrap" id="thesis">
        <div className="section-head"><div><div className="eyebrow eyebrow-dark">01 — ИНВЕСТИЦИОННЫЙ ТЕЗИС</div><h2>Не ещё один бот.<br /><em>Отдача от уже созданного.</em></h2></div><p>По данным материалов кейса, AI-эффект Сбера за 2025 год — 475 млрд ₽, из них более 50 млрд ₽ приходится на генеративный AI; ориентир 2026 года — 550 млрд ₽. Следующий шаг — связать существующие модели, агентов и каналы с измеримой стоимостью полезного результата. ЯдроСбер помогает получать больше ценности от уже созданных активов.</p></div>
        <div className="feature-grid">{valueStreams.map(({ icon: Icon, number, title, text }) => <article className="feature-card" key={number}><span className="feature-number">{number}</span><div className="feature-icon"><Icon size={22} strokeWidth={1.7} /></div><h3>{title}</h3><p>{text}</p><ArrowUpRight className="feature-arrow" size={18} /></article>)}</div>
      </section>

      <section className="impact" id="model"><div className="impact-inner"><div className="eyebrow">02 — ЧТО ПОКУПАЕТ БИЗНЕС</div><h2>Не больше диалогов.<br /><span>Больше результата с каждого.</span></h2><div className="impact-bottom"><p>Экономика складывается из стоимости успешной AI-задачи, времени поддержки и сотрудников, а также вклада новых активных бизнес-клиентов. API-экономию не считаем внутренней экономией Сбера без подтверждения себестоимости.</p><div className="impact-stat"><strong>3 <small>→</small></strong><span>источника эффекта<br />одна система измерения</span></div></div><div className="impact-art"><div className="impact-ring ring-a"/><div className="impact-ring ring-b"/><div className="impact-ring ring-c"/><div className="impact-dot"><Layers3 size={28}/></div></div></div></section>

      <section className="economics section-wrap"><div className="section-head"><div><div className="eyebrow eyebrow-dark">03 — СЦЕНАРНАЯ ЭКОНОМИКА · 2027–2029</div><h2>Проверяемый план.<br /><em>Не обещание дохода.</em></h2></div><p>Базовая модель команды сравнивает ЯдроСбер с инерционным развитием. Эффект труда учитывается только в доле, конвертируемой в результат; вклад бизнес-клиентов — с 50% поправкой на инкрементальность. Это сценарий, а не бюджет или прогноз Сбера.</p></div><div className="research-note"><strong>Базовый сценарий: 5,65 млрд ₽ инвестиций за 3 года</strong><span>Затраты: 2,40 / 1,80 / 1,45 млрд ₽ в 2027–2029. Эффект труда + вклад бизнес-клиентов: 0,80 / 3,52 / 9,16 млрд ₽ в год. Годовой чистый результат: −1,60 / +1,72 / +7,71 млрд ₽; накопленный становится положительным в 2028 году (+0,12 млрд ₽), к концу 2029-го — +7,83 млрд ₽.</span></div><div className="model-grid">{[{name:'2027 · Пилот и фундамент',sign:'−',key:'y2027',description:'Эффект 0,80 млрд ₽; затраты 2,40 млрд ₽; чистый результат −1,60 млрд ₽. Проверяем стоимость задачи и причинный вклад сценария.'},{name:'2028 · Выход в плюс',sign:'+',key:'y2028',description:'Эффект 3,52 млрд ₽; затраты 1,80 млрд ₽; годовой результат +1,72 млрд ₽. Накопленный эффект модели: +0,12 млрд ₽.'},{name:'2029 · Масштабирование',sign:'+',key:'y2029',description:'Эффект 9,16 млрд ₽; затраты 1,45 млрд ₽; годовой результат +7,71 млрд ₽. Накопленный эффект: +7,83 млрд ₽.'}].map((item) => <article key={item.key}><span>{item.sign}</span><h3>{item.name}</h3><p>{item.description}</p></article>)}<div className="model-result"><span>СТРЕСС-СЦЕНАРИЙ · 50% ЭФФЕКТА</span><strong>Окупаемость в 2029 году</strong><p>Накопленный результат: −2,00 / −2,04 / +1,09 млрд ₽. Если пилот не подтверждает экономику — проект не масштабируется.</p></div></div><p className="model-caveat">В базовой модели эффект API исключён: внутренняя себестоимость Сбера неизвестна. Отдельная модель perp2.txt оценивает зрелый годовой эффект до 14,8 млрд ₽ к 2029 году, но опирается на более агрессивные допущения по churn, автоматизации обращений и охвату; это альтернативный, не суммируемый с базовым сценарий. Публичные API-тарифы — только ориентир.</p></section>

      <section className="pilot" id="pilot"><div className="pilot-inner"><div className="eyebrow">04 — СЦЕНАРИЙ РОСТА И ПЛАН ПИЛОТА</div><h2>От клиента банка<br /><span>к активному бизнесу.</span></h2><p className="pilot-intro">Модель воронки 2027 года: при охвате 10% базы GigaChat и последовательных конверсиях расчёт даёт 31,5 тыс. оформлений ИП против 10 тыс. в инерционном сценарии. После 50% поправки на людей, которые открылись бы и без продукта, модель принимает 10,75 тыс. дополнительных активных бизнес-клиентов. Это гипотеза для A/B-проверки, не обещание продаж.</p><div className="pilot-steps">{useCases.map((item) => <article key={item.index}><span>{item.index}</span><div><h3>{item.title}</h3><p>{item.detail}</p></div></article>)}</div><div className="pilot-gates"><div><span>90 ДНЕЙ · ДИЗАЙН</span><strong>Контроль и критерии</strong><p>Одна задача, рандомизация по пользователю, метрики и базовый уровень до старта.</p></div><div><span>90 ДНЕЙ · ИЗМЕРЕНИЕ</span><strong>Экономика без самообмана</strong><p>Цена успешной задачи, качество, повторы, согласия и реально высвобождённое время.</p></div><div><span>90 ДНЕЙ · РЕШЕНИЕ</span><strong>Масштабировать доказанное</strong><p>Цель: стоимость задачи −25%, качество не хуже, ноль несанкционированных переносов.</p></div></div></div></section>

      <section className="principles section-wrap" id="governance"><div className="principles-heading"><div className="eyebrow eyebrow-dark">05 — ДОВЕРИЕ И УПРАВЛЯЕМОСТЬ</div><h2>Один помощник.<br /><em>Изолированные контуры.</em></h2><p>Юридическая безопасность — проектный принцип и предмет проверки юристами Сбера, а не обещание автоматического соответствия законам.</p></div><div className="principle-list"><article><span>01</span><div><h3>Личное отдельно от бизнеса и работы</h3><p>Раздельные контексты retail / business / employee; общий интерфейс не означает общую базу.</p></div><ShieldCheck /></article><article><span>02</span><div><h3>Явное, целевое согласие</h3><p>Межролевой перенос — только для конкретной цели и объёма данных, с фиксацией разрешения.</p></div><Fingerprint /></article><article><span>03</span><div><h3>Отзыв, аудит, подтверждение</h3><p>Согласие можно отозвать; действия агента логируются; значимые шаги подтверждает человек.</p></div><Sparkles /></article></div></section>

      <section className="closing" id="decision"><div className="closing-orb"/><div className="eyebrow">ЯДРОСБЕР / РЕШЕНИЕ ДЛЯ РУКОВОДСТВА</div><h2>Не финансировать обещание.<br /><span>Купить доказательство.</span></h2><p>Утвердить 90-дневный пилот одного сценария, владельца со стороны бизнеса и контрольную группу. Предварительная модель: инвестиции 5,65 млрд ₽ за три года, положительный накопленный эффект в 2028-м; при реализации половины эффекта — в 2029-м. Следующий транш — только после подтверждения на данных Сбера.</p><a className="button button-dark" href="#pilot">Метрики и условия пилота <ArrowRight size={17} /></a><div className="closing-sign">С<span>•</span>1</div></section>
    </main>

    <footer><a className="brand footer-brand" href="#top"><span className="brand-mark"><span /></span><span>Сбер<span className="brand-light">Один</span></span></a><span>Концепция продукта · сценарные оценки требуют проверки</span><span>2026</span></footer>
  </>;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
