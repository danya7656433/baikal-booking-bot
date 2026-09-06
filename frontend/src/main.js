import { createIcons, Waves, MapPin, CalendarDays, Users, ArrowRight, ArrowLeft, LogIn, LogOut, X, Camera, Check, Search, Clock, ShieldCheck, Phone, Settings, ClipboardList, ChevronLeft, ChevronRight, RefreshCw, Eye, Plus, Trash2, Upload, Receipt, Pencil, Send } from 'lucide';
import './style.css';
import { renderLanding, mountLanding } from './landing.js';
import { ArrowUpRight, ArrowDown, Pause, Play } from 'lucide';

const icons = { Waves, MapPin, CalendarDays, Users, ArrowRight, ArrowLeft, LogIn, LogOut, X, Camera, Check, Search, Clock, ShieldCheck, Phone, Settings, ClipboardList, ChevronLeft, ChevronRight, RefreshCw, Eye, Plus, Trash2, Upload, Receipt, Pencil, Send };
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
const money = n => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(n);
const dateLabel = s => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(new Date(`${s}T12:00:00`));
const addDays = (s, n) => { const d = new Date(`${s}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const s = { config: null, user: null, token: sessionStorage.getItem('baikal-token'), view: 'book', options: [], search: null, tab: 'bookings', offset: 0, q: '', status: '', gallery: null, calendar: null };
const dialog = $('#dialog');
let renderId = 0, toastTimer, receiptUrl;

let disposeLanding = () => {};
function paint() { createIcons({ icons: { ...icons, ArrowUpRight, ArrowDown, Pause, Play } }); }
function toast(message) { const el = $('#toast'); el.textContent = message; el.style.display = 'block'; clearTimeout(toastTimer); toastTimer = setTimeout(() => el.style.display = 'none', 5000); }
function closeDialog() { dialog.close(); if (receiptUrl) { URL.revokeObjectURL(receiptUrl); receiptUrl = null; } }
function modal(title, body, subtitle = '') {
  dialog.innerHTML = `<div class="dialog-head"><div><h2 id="dialog-title">${esc(title)}</h2>${subtitle ? `<p class="dialog-subtitle">${esc(subtitle)}</p>` : ''}</div><button class="icon" data-action="close" aria-label="Закрыть" title="Закрыть">${icon('x')}</button></div>${body}`;
  if (!dialog.open) dialog.showModal();
  paint();
}
async function api(path, { method = 'GET', body, blob = false } = {}) {
  const headers = s.token ? { Authorization: `Bearer ${s.token}` } : {};
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api${path}`, { method, headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  if (!response.ok) {
    let error; try { error = await response.json(); } catch { error = {}; }
    if (response.status === 401) { s.token = null; s.user = null; sessionStorage.removeItem('baikal-token'); }
    throw new Error(typeof error.detail === 'string' ? error.detail : `Ошибка запроса (${response.status})`);
  }
  return blob ? response.blob() : response.json();
}
async function authenticated(token) {
  s.token = token; sessionStorage.setItem('baikal-token', token); s.user = await api('/me'); closeDialog(); await render();
}
function room(code) { return s.config.rooms.find(r => r.code === code); }
function cover(code) { const r = room(code); return code === '2' ? '/photos/2_room/photo3.jpg' : r?.photos[0] || '/photos/territory/photo1.jpg'; }
function formError() { return '<p class="form-error" role="alert"></p>'; }
function badge(b) { return `<span class="badge ${esc(b.status)}">${esc(b.status_label)}</span>`; }
function input(label, name, value = '', type = 'text', extra = '') { return `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`; }
function number(label, name, value, min = 0, max = 14) { return input(label, name, value, 'number', `min="${min}" max="${max}" step="1" required`); }
const statusLabels = { new: 'Новая заявка', pending: 'В обработке', awaiting_payment: 'Ожидает оплаты', awaiting_payment_confirmation: 'Чек на проверке', paid: 'Оплачено', awaiting_cancellation: 'Запрос отмены', cancelled: 'Отменена', rejected: 'Отклонена' };
function header() {
  return `<header class="topbar"><div class="container topbar-inner"><a class="brand" href="#book">${icon('waves')}<span>Дача на Байкале</span></a><nav class="nav" aria-label="Основная навигация"><a href="#book" class="${s.view === 'book' ? 'active' : ''}">${icon('calendar-days')}Бронирование</a><a href="#bookings" class="${s.view === 'bookings' ? 'active' : ''}">${icon('clipboard-list')}Мои брони</a>${s.user?.is_admin ? `<a href="#admin" class="${s.view === 'admin' ? 'active' : ''}">${icon('settings')}Управление</a>` : ''}</nav><div class="account">${s.user ? `<span class="account-name">${esc(s.user.name)}</span><button class="icon" data-action="logout" aria-label="Выйти" title="Выйти">${icon('log-out')}</button>` : `<button data-action="login">${icon('log-in')}Войти</button>`}</div></div></header>${s.config.demo ? `<div class="demo-bar"><div class="container demo-inner"><span>Демо</span><select id="demo-role" aria-label="Роль в демо"><option value="guest" ${!s.user?.is_admin ? 'selected' : ''}>Гость</option><option value="admin" ${s.user?.is_admin ? 'selected' : ''}>Администратор</option></select></div></div>` : ''}`;
}
function footer() {
  return `<footer class="footer"><div class="container footer-inner"><div>Дача на Байкале · Максимиха</div><div class="footer-links"><a href="tel:+79085948948">${esc(s.config.contact)}</a><button class="link" data-action="rules">Правила</button><a href="${esc(s.config.map_url)}" target="_blank" rel="noopener noreferrer">На карте ↗</a></div></div></footer>`;
}
function intro(title, eyebrow = 'Личное пространство') { return `<div class="page-intro"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1></div></div>`; }
async function render() {
  const current = ++renderId;
  disposeLanding(); disposeLanding = () => {};
  s.view = ['home', 'book', 'bookings', 'admin'].includes(location.hash.slice(1)) ? location.hash.slice(1) : (window.Telegram?.WebApp?.initData ? 'book' : 'home');
  document.body.classList.toggle('is-home', s.view === 'home');
  document.title = s.view === 'home' ? 'Дача на Байкале · Максимиха' : 'Дача на Байкале · Бронирование';
  $('#app').innerHTML = `${header()}<main id="main" tabindex="-1"></main>${s.view === 'home' ? '' : footer()}`;
  $('.brand').href = '#home';
  if (s.view === 'home') {
    const loginButton = $('[data-action="login"]');
    if (loginButton) { loginButton.innerHTML = icon('log-in'); loginButton.setAttribute('aria-label', 'Войти через Telegram'); loginButton.title = 'Войти через Telegram'; }
    $('.account').insertAdjacentHTML('afterbegin', `<a class="home-account-link" href="#bookings" title="Мои брони" aria-label="Мои брони">${icon('clipboard-list')}</a>`);
  }
  try {
    if (s.view === 'home') {
      $('#main').innerHTML = renderLanding();
      paint();
      const dispose = await mountLanding();
      if (current === renderId) disposeLanding = dispose;
      else dispose();
    }
    else if (s.view === 'book') { renderSearch(); await search(current); }
    else if (s.view === 'bookings') await renderBookings(current);
    else await renderAdmin(current);
  } catch (error) { if (current === renderId) $('#main').innerHTML = `<div class="container"><div class="error" style="margin-top:30px">${esc(error.message)}</div><button data-action="refresh" style="margin-top:18px">${icon('refresh-cw')}Повторить</button></div>`; }
  paint();
}
function renderSearch() {
  const d = s.search;
  $('#main').innerHTML = `<div class="container"><div class="page-intro"><div><p class="eyebrow">Максимиха · озеро Байкал</p><h1>Дача на Байкале</h1><p class="location">${icon('map-pin')}${esc(s.config.address)}</p></div><div class="season-tag"><span class="dot"></span>${s.config.season_active ? 'Бронирование открыто' : 'Приём заявок закрыт'} · ${s.config.booking_year}</div></div></div><section class="search-band"><div class="container"><form id="search-form" class="search-form" data-form="search">${input('Заезд', 'date_from', d.date_from, 'date', `min="${addDays(s.config.today, 1)}" required`)}${input('Выезд', 'date_to', d.date_to, 'date', `min="${addDays(d.date_from, 1)}" required`)}${number('Взрослые', 'adults', d.adults, 1)}${number('Дети', 'children', d.children)}${number('Детские места', 'children_beds', d.children_beds)}<button class="primary" type="submit">${icon('search')}Найти</button></form></div></section><div class="container"><section id="results" aria-live="polite"><div class="empty" aria-busy="true">Проверяем свободные даты…</div></section><div class="facts"><div class="fact">${icon('clock')}<div><strong>Заезд с 14:00</strong><p>Выезд до 12:00</p></div></div><div class="fact">${icon('shield-check')}<div><strong>Предоплата от 50%</strong><p>После подтверждения заявки</p></div></div><div class="fact">${icon('phone')}<div><strong>На связи с вами</strong><p><a href="tel:+79085948948">+7 (908) 594-89-48</a></p></div></div></div></div>`;
}
async function search(current = renderId) {
  const data = await api('/availability', { method: 'POST', body: s.search });
  if (current !== renderId || !$('#results')) return;
  s.options = data.options;
  const singles = data.options.filter(o => o.parts.length === 1);
  const combos = data.options.filter(o => o.parts.length > 1);
  $('#results').innerHTML = `<div class="results-top"><div><h2>Варианты проживания</h2><p>${dateLabel(s.search.date_from)} — ${dateLabel(s.search.date_to)} · ${s.search.adults} взрослых${s.search.children ? ` · ${s.search.children} детей` : ''}</p></div><button data-action="calendar">${icon('calendar-days')}Свободные даты</button></div>${singles.length ? `<div class="room-grid">${singles.map(o => { const r = room(o.code); return `<article class="room-card"><div class="room-photo"><img src="${cover(o.code)}" alt="${esc(r.name)}" loading="lazy"><span class="availability ${o.available ? '' : 'busy'}">${o.available ? 'Свободно на ваши даты' : 'Занято на ваши даты'}</span><button class="photo-control" data-action="gallery" data-code="${esc(o.code)}">${icon('camera')}${r.photos.length} фото</button></div><div class="room-body"><h3>${esc(r.name)}</h3><div class="room-meta">${icon('users')}До ${o.capacity} гостей</div><p class="room-description">${esc(r.description)}</p><div class="room-bottom"><div class="price">${money(o.total)}<small>за весь период · ${o.nights} ноч.</small></div><button class="${o.available ? 'primary' : ''}" data-action="reserve" data-code="${esc(o.code)}" ${o.available ? '' : 'disabled'}>${o.available ? 'Выбрать' : 'Занято'}${icon('arrow-right')}</button></div></div></article>`; }).join('')}</div>` : '<div class="alert">Для вашей компании подойдут несколько номеров или весь дом.</div>'}${combos.length ? `<details class="combo-section" ${singles.length ? '' : 'open'}><summary>Несколько номеров и весь дом</summary><div class="combo-list">${combos.map(o => `<div class="combo-row"><div><strong>${esc(o.name)}</strong><small>До ${o.capacity} гостей · ${o.available ? 'Свободно' : 'Занято'}</small></div><div class="price">${money(o.total)}</div><button class="icon" title="Выбрать ${esc(o.name)}" aria-label="Выбрать ${esc(o.name)}" data-action="reserve" data-code="${esc(o.code)}" ${o.available ? '' : 'disabled'}>${icon('arrow-right')}</button></div>`).join('')}</div></details>` : ''}`;
  paint();
}
function rulesBody() { return `<div class="policy"><p>Заезд после 14:00, выезд до 12:00. Бронирование доступно минимум за день до заезда, на срок от 1 до 31 ночи.</p><p>Заявку подтверждает администратор. После подтверждения необходимо внести предоплату от 50% в течение 3 часов и приложить чек. Оплата переводом.</p><p>Дети без отдельного спального места не входят в расчёт платных мест. Дети с отдельным местом оплачиваются по тарифу гостя.</p><p>Отмена рассматривается администратором. По действующим правилам объекта при отмене менее чем за 7 дней до заезда удерживается 50% стоимости. Возврат согласуется с администратором.</p></div>`; }
function login() {
  modal('Вход в личный кабинет', `<div id="telegram-login"></div>${!s.config.bot_username ? '<p class="alert">Вход временно недоступен. Свяжитесь с администратором по телефону +7 (908) 594-89-48.</p>' : ''}`);
  if (s.config.demo) return api('/auth/demo?role=guest', { method: 'POST' }).then(data => authenticated(data.token));
  if (s.config.bot_username) {
    const script = document.createElement('script'); script.src = 'https://telegram.org/js/telegram-widget.js?22'; script.async = true;
    script.dataset.telegramLogin = s.config.bot_username; script.dataset.size = 'large'; script.dataset.onauth = 'onTelegramAuth(user)';
    $('#telegram-login').append(script);
  }
}
window.onTelegramAuth = user => api('/auth/widget', { method: 'POST', body: user }).then(data => authenticated(data.token)).catch(error => toast(error.message));
function reserve(code) {
  if (!s.user) return login();
  const option = s.options.find(o => o.code === code);
  if (!option?.available) return;
  s.reserving = { ...s.search, room_type: code, quoted_total: option.total, request_key: crypto.randomUUID() };
  modal('Заявка на бронирование', `<div class="dialog-summary"><span>${esc(option.name)}<br><small>${dateLabel(s.search.date_from)} — ${dateLabel(s.search.date_to)}</small></span><span class="price">${money(option.total)}</span></div><form class="stack" data-form="reserve">${input('Имя и фамилия', 'full_name', s.user.name === 'Гость' ? '' : s.user.name, 'text', 'autocomplete="name" minlength="2" maxlength="120" required')}${input('Телефон', 'phone', '', 'tel', 'autocomplete="tel" placeholder="+7 900 000-00-00" minlength="10" maxlength="25" required')}<label>Комментарий<textarea name="comment" maxlength="1500" placeholder="Пожелания к проживанию"></textarea></label><details><summary>Условия бронирования и отмены</summary>${rulesBody()}</details><label class="check"><input type="checkbox" name="rules_accepted" required><span>Принимаю условия бронирования и отмены, согласен на обработку контактных данных для оформления заявки.</span></label>${formError()}<button class="primary" type="submit">${icon('check')}Отправить заявку</button></form>`, 'Оплата после подтверждения администратором');
}
async function renderBookings(current) {
  if (!s.user) { $('#main').innerHTML = `<div class="container">${intro('Мои брони')}<div class="empty">${icon('clipboard-list')}<h3>Ваши поездки на Байкал</h3><button class="primary" data-action="login">${icon('log-in')}Войти через Telegram</button></div></div>`; return; }
  const bookings = await api('/bookings'); if (current !== renderId) return;
  $('#main').innerHTML = `<div class="container">${intro('Мои брони')}<div class="booking-list">${bookings.length ? bookings.map(b => `<article class="booking-row"><img src="${cover(b.room_type.split('+')[0])}" alt=""><div><h3>${esc(b.room_name)}</h3><small>№${b.id} · ${dateLabel(b.date_from)} — ${dateLabel(b.date_to)}</small></div><div class="booking-status">${badge(b)}</div><div class="booking-money"><strong>${money(b.total)}</strong><small>Остаток ${money(b.remaining)}</small></div><button data-action="detail" data-id="${b.id}"><span class="button-label">Подробнее</span>${icon('arrow-right')}</button></article>`).join('') : `<div class="empty">${icon('calendar-days')}<h3>Бронирований пока нет</h3><a href="#book"><button class="primary">Выбрать даты ${icon('arrow-right')}</button></a></div>`}</div></div>`;
}
async function detail(id) {
  const b = await api(`/bookings/${id}`); s.detail = b;
  const canCancel = ['new', 'pending', 'awaiting_payment', 'awaiting_payment_confirmation', 'paid'].includes(b.status);
  modal(`Заявка №${b.id}`, `${badge(b)}<div class="detail-grid"><div><small>Проживание</small><strong>${esc(b.room_name)}</strong></div><div><small>Даты</small><strong>${dateLabel(b.date_from)} — ${dateLabel(b.date_to)}</strong></div><div><small>Гость</small><strong>${esc(b.full_name)}</strong></div><div><small>Телефон</small><strong>${esc(b.phone)}</strong></div></div><div class="dialog-summary"><span>Стоимость проживания</span><strong>${money(b.total)}</strong><span>Внесено / возвращено</span><span>${money(b.paid)} / ${money(b.refunds)}</span><strong>Осталось оплатить</strong><strong>${money(b.remaining)}</strong></div>${b.comment ? `<p>${esc(b.comment)}</p>` : ''}${['awaiting_payment', 'paid'].includes(b.status) && b.remaining > 0 ? `<div class="alert" style="margin-top:18px">${esc(s.config.payment_instructions)}<br>Минимальная предоплата: ${money(b.required_prepayment)}</div><form class="stack" data-form="receipt" data-id="${b.id}" style="margin-top:18px"><label>Чек перевода<input type="file" name="file" accept="image/jpeg,image/png,image/webp" required></label>${formError()}<button type="submit" class="primary">${icon('upload')}Отправить чек</button></form>` : ''}${b.receipts.length ? `<h3 style="margin-top:24px">Чеки</h3>${b.receipts.map(r => `<div class="receipt-row"><span>Чек №${r.id} · ${esc({ pending: 'На проверке', approved: 'Подтверждён', rejected: 'Отклонён' }[r.status])}</span><div class="actions"><button class="icon" data-action="receipt-image" data-id="${r.id}" aria-label="Посмотреть чек" title="Посмотреть чек">${icon('eye')}</button>${s.user.is_admin && r.status === 'pending' ? `<button data-action="approve-receipt" data-id="${r.id}">${icon('check')}Проверить</button>` : ''}</div>${r.note ? `<p class="muted">${esc(r.note)}</p>` : ''}</div>`).join('')}` : ''}<div class="actions">${s.user.is_admin && ['new', 'pending'].includes(b.status) ? `<button class="primary" data-action="stage" data-status="awaiting_payment" data-id="${b.id}">${icon('check')}Подтвердить заявку</button><button class="danger" data-action="stage" data-status="rejected" data-id="${b.id}">Отклонить</button>` : ''}${s.user.is_admin && b.status === 'awaiting_cancellation' ? `<button class="danger" data-action="stage" data-status="cancelled" data-id="${b.id}">Подтвердить отмену</button>` : ''}${s.user.is_admin && b.net_paid > 0 ? `<button data-action="refund" data-id="${b.id}">Учесть возврат</button>` : ''}${canCancel ? `<button class="link" data-action="cancel-booking" data-id="${b.id}">Запросить отмену</button>` : ''}</div>${b.history ? `<details class="history"><summary>История изменений</summary>${b.history.map(h => `<p>${esc(h.created_at.slice(0,16).replace('T',' '))} · ${esc(h.kind)}</p>`).join('')}</details>` : ''}`);
}
function gallery(code, index = 0) { const r = room(code); if (!r?.photos.length) return; s.gallery = { code, index: (index + r.photos.length) % r.photos.length }; modal(r.name, `<img class="gallery-image" src="${esc(r.photos[s.gallery.index])}" alt="${esc(r.name)}"><div class="gallery-controls"><button class="icon" data-action="gallery-prev" aria-label="Предыдущее фото" title="Предыдущее фото">${icon('chevron-left')}</button><span>${s.gallery.index + 1} / ${r.photos.length}</span><button class="icon" data-action="gallery-next" aria-label="Следующее фото" title="Следующее фото">${icon('chevron-right')}</button></div>`); }
async function calendar(code = '2', month = `${s.search.date_from.slice(0,7)}-01`) {
  s.calendar = { code, month };
  const data = await api(`/calendar?room_type=${encodeURIComponent(code)}&month=${month}`);
  const weekday = (new Date(`${month}T12:00:00`).getDay() + 6) % 7;
  modal('Календарь занятости', `<label style="margin-bottom:20px">Номер<select id="calendar-room">${s.config.rooms.map(r => `<option value="${r.code}" ${r.code === code ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}</select></label><div class="calendar-head"><button class="icon" data-action="month-prev" aria-label="Предыдущий месяц" title="Предыдущий месяц">${icon('chevron-left')}</button><h3>${new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(new Date(`${month}T12:00:00`))}</h3><button class="icon" data-action="month-next" aria-label="Следующий месяц" title="Следующий месяц">${icon('chevron-right')}</button></div><div class="calendar-grid">${['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(d => `<span>${d}</span>`).join('')}${'<span></span>'.repeat(weekday)}${data.days.map(d => `<button data-action="select-day" data-date="${d.date}" ${d.available && d.date > s.config.today ? '' : 'disabled'} title="${d.date}: ${d.available ? 'свободно' : 'занято'}">${Number(d.date.slice(-2))}</button>`).join('')}</div><div class="legend"><span><i></i>Свободно</span><span><i class="busy"></i>Занято</span></div>`);
}
async function renderAdmin(current) {
  if (!s.user?.is_admin) { $('#main').innerHTML = `<div class="container"><div class="empty"><h3>Доступ только для администратора</h3></div></div>`; return; }
  $('#main').innerHTML = `<div class="container">${intro('Управление', 'Дача на Байкале')}<nav class="admin-tabs" aria-label="Разделы управления">${[['bookings','Заявки'],['rooms','Объекты'],['prices','Цены и сезон'],['blocks','Блокировки дат']].map(([key,label]) => `<button data-action="admin-tab" data-tab="${key}" class="${s.tab === key ? 'active' : ''}">${label}</button>`).join('')}</nav><div id="admin-content" aria-live="polite"></div></div>`;
  const el = $('#admin-content');
  if (s.tab === 'bookings') {
    const data = await api(`/admin/bookings?q=${encodeURIComponent(s.q)}&status=${encodeURIComponent(s.status)}&offset=${s.offset}`); if (current !== renderId) return;
    el.innerHTML = `<form class="toolbar" data-form="admin-search"><label class="wide">Поиск<input name="q" value="${esc(s.q)}" placeholder="Имя, телефон или номер заявки"></label><label>Статус<select name="status"><option value="">Все статусы</option>${Object.entries(statusLabels).map(([key,label]) => `<option value="${key}" ${key === s.status ? 'selected' : ''}>${label}</option>`).join('')}</select></label><button type="submit">${icon('search')}Найти</button><button type="button" data-action="refresh" class="icon" title="Обновить" aria-label="Обновить">${icon('refresh-cw')}</button></form><div class="table-wrap"><table><thead><tr><th>Заявка</th><th>Гость</th><th>Проживание</th><th>Статус</th><th>Сумма / остаток</th><th></th></tr></thead><tbody>${data.bookings.map(b => `<tr><td>#${b.id}</td><td><strong>${esc(b.full_name)}</strong><small>${esc(b.phone)}</small></td><td>${esc(b.room_name)}<small>${dateLabel(b.date_from)} — ${dateLabel(b.date_to)}</small></td><td>${badge(b)}</td><td>${money(b.total)}<small>${money(b.remaining)}</small></td><td><button class="icon" data-action="detail" data-id="${b.id}" title="Открыть заявку ${b.id}" aria-label="Открыть заявку ${b.id}">${icon('arrow-right')}</button></td></tr>`).join('')}</tbody></table></div>${!data.total ? '<div class="empty">Заявки не найдены</div>' : ''}<div class="pagination"><span class="muted">Всего: ${data.total}</span><div class="actions"><button class="icon" data-action="page-prev" ${s.offset ? '' : 'disabled'} title="Предыдущая страница" aria-label="Предыдущая страница">${icon('chevron-left')}</button><button class="icon" data-action="page-next" ${s.offset + 50 < data.total ? '' : 'disabled'} title="Следующая страница" aria-label="Следующая страница">${icon('chevron-right')}</button></div></div>`;
  } else if (s.tab === 'rooms') {
    el.innerHTML = s.config.rooms.map(r => `<article class="object-row"><img src="${cover(r.code)}" alt="${esc(r.name)}"><form class="stack" data-form="room" data-code="${r.code}"><h3>${esc(r.name)} · ${r.capacity} места</h3><label>Описание<textarea name="description" minlength="3" maxlength="2000" required>${esc(r.description)}</textarea></label><div class="actions"><label class="check"><input type="checkbox" name="is_available" ${r.is_available ? 'checked' : ''}>Открыт для новых бронирований</label><button type="submit">${icon('check')}Сохранить</button></div>${formError()}</form></article>`).join('');
  } else if (s.tab === 'prices') {
    const prices = await api('/admin/prices'); if (current !== renderId) return;
    el.innerHTML = `<div class="settings-grid"><section><h3>Стоимость места за ночь</h3><form class="stack" data-form="prices"><div class="form-row">${input('С даты', 'date_from', s.search.date_from, 'date', 'required')}${input('По дату включительно', 'date_to', s.search.date_to, 'date', 'required')}</div>${number('Цена, ₽ / место / ночь', 'price', 1500, 1, 1000000)}${formError()}<button class="primary" type="submit">${icon('check')}Установить цену</button></form><p class="muted" style="margin-top:15px;font-size:12px">Цена сохранённых заявок не меняется. Для всего дома при 8–10 гостях действует отдельный фиксированный тариф.</p></section><section><h3>Сезон бронирования</h3><form class="stack" data-form="season">${number('Год', 'year', s.config.booking_year, 2026, 2100)}<label class="check"><input type="checkbox" name="is_active" ${s.config.season_active ? 'checked' : ''}>Принимать новые заявки</label>${formError()}<button type="submit">Сохранить сезон</button></form></section></div><h3 style="margin-bottom:15px">Календарные цены</h3><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Цена за место</th></tr></thead><tbody>${prices.map(p => `<tr><td>${esc(p.date)}</td><td>${money(p.price)}</td></tr>`).join('')}</tbody></table></div>${!prices.length ? '<p class="muted" style="padding:20px 0">Установлен базовый тариф: 1 500 ₽ за место в сутки.</p>' : ''}`;
  } else {
    const blocks = await api('/admin/blocks'); if (current !== renderId) return;
    el.innerHTML = `<form class="stack" data-form="block" style="max-width:620px;margin-bottom:30px"><label>Объект<select name="room_type">${s.config.rooms.map(r => `<option value="${r.code}">${esc(r.name)}</option>`).join('')}<option value="all">Все номера</option></select></label><div class="form-row">${input('С даты', 'date_from', s.search.date_from, 'date', 'required')}${input('До даты, не включая', 'date_to', s.search.date_to, 'date', 'required')}</div>${input('Причина', 'reason', '', 'text', 'minlength="3" maxlength="200" required')}${formError()}<button class="primary" type="submit">${icon('plus')}Заблокировать даты</button></form><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Объект</th><th>Причина</th><th></th></tr></thead><tbody>${blocks.map(b => `<tr><td>${b.date}</td><td>${esc(room(b.room_type)?.name || 'Все номера')}</td><td>${esc(b.reason)}</td><td><button class="icon" data-action="unblock" data-id="${b.id}" aria-label="Снять блокировку ${b.date}" title="Снять блокировку">${icon('trash-2')}</button></td></tr>`).join('')}</tbody></table></div>`;
  }
}
function receiptReview(id) {
  modal('Проверка перевода', `<form class="stack" data-form="approve-receipt" data-id="${id}">${number('Фактически полученная сумма, ₽', 'amount', Math.min(s.detail.remaining, s.detail.required_prepayment), 1, 10000000)}<label>Комментарий<textarea name="note" maxlength="1000"></textarea></label><label class="check"><input type="checkbox" required>Деньги поступили на счёт</label>${formError()}<button type="submit" class="primary">${icon('check')}Подтвердить оплату</button><button type="button" data-action="receipt-image" data-id="${id}">${icon('eye')}Посмотреть чек</button><button type="button" class="danger" data-action="reject-receipt" data-id="${id}">Отклонить чек</button></form>`);
}
const actions = {
  close: closeDialog, login, refresh: render,
  logout: async () => { await api('/auth/logout', { method: 'POST' }); s.user = null; s.token = null; sessionStorage.removeItem('baikal-token'); await render(); },
  rules: () => modal('Правила бронирования', rulesBody()),
  reserve: el => reserve(el.dataset.code),
  gallery: el => gallery(el.dataset.code), 'gallery-prev': () => gallery(s.gallery.code, s.gallery.index - 1), 'gallery-next': () => gallery(s.gallery.code, s.gallery.index + 1),
  calendar: () => calendar(),
  'month-prev': () => calendar(s.calendar.code, addDays(s.calendar.month, -1).slice(0,7) + '-01'),
  'month-next': () => calendar(s.calendar.code, addDays(s.calendar.month, 32).slice(0,7) + '-01'),
  'select-day': async el => { const nights = Math.round((Date.parse(s.search.date_to) - Date.parse(s.search.date_from)) / 86400000); s.search.date_from = el.dataset.date; s.search.date_to = addDays(el.dataset.date, nights); closeDialog(); await render(); },
  detail: el => detail(el.dataset.id),
  'admin-tab': async el => { s.tab = el.dataset.tab; await render(); }, 'page-prev': async () => { s.offset = Math.max(0, s.offset - 50); await render(); }, 'page-next': async () => { s.offset += 50; await render(); },
  stage: async el => { await api(`/admin/bookings/${el.dataset.id}/status`, { method: 'POST', body: { status: el.dataset.status } }); await detail(el.dataset.id); toast('Статус сохранён'); },
  'cancel-booking': el => modal('Запрос на отмену', `<form class="stack" data-form="cancel" data-id="${el.dataset.id}"><label>Причина отмены<textarea name="note" minlength="3" maxlength="1500" required></textarea></label>${formError()}<button type="submit" class="danger">Отправить запрос</button></form>`),
  'receipt-image': async el => { const blob = await api(`/receipts/${el.dataset.id}/image`, { blob: true }); if (receiptUrl) URL.revokeObjectURL(receiptUrl); receiptUrl = URL.createObjectURL(blob); modal('Чек перевода', `<img class="receipt-preview" src="${receiptUrl}" alt="Чек перевода"><div class="actions"><button data-action="detail" data-id="${s.detail.id}">${icon('arrow-left')}К заявке</button>${s.user.is_admin ? `<button data-action="approve-receipt" data-id="${el.dataset.id}">Проверить</button>` : ''}</div>`); },
  'approve-receipt': el => receiptReview(el.dataset.id),
  'reject-receipt': el => modal('Отклонение чека', `<form class="stack" data-form="reject-receipt" data-id="${el.dataset.id}"><label>Причина<textarea name="note" minlength="3" maxlength="1500" required></textarea></label>${formError()}<button type="submit" class="danger">Отклонить чек</button></form>`),
  refund: el => modal('Учёт возврата', `<form class="stack" data-form="refund" data-id="${el.dataset.id}">${number('Сумма выполненного возврата, ₽', 'amount', s.detail.net_paid, 1, s.detail.net_paid)}<label>Основание<textarea name="note" minlength="3" maxlength="1000" required></textarea></label><label class="check"><input type="checkbox" required>Возврат денег гостю уже выполнен</label>${formError()}<button type="submit" class="primary">Учесть возврат</button></form>`),
  unblock: async el => { await api(`/admin/blocks/${el.dataset.id}`, { method: 'DELETE' }); await render(); toast('Блокировка снята'); },
};
document.addEventListener('click', async event => {
  const el = event.target.closest('[data-action]'); if (!el || el.disabled) return;
  const action = actions[el.dataset.action]; if (!action) return;
  el.disabled = true;
  try { await action(el); } catch (error) { toast(error.message); } finally { if (el.isConnected) el.disabled = false; paint(); }
});
document.addEventListener('change', async event => {
  try {
    if (event.target.id === 'demo-role') { const data = await api(`/auth/demo?role=${event.target.value}`, { method: 'POST' }); await authenticated(data.token); }
    if (event.target.id === 'calendar-room') await calendar(event.target.value, s.calendar.month);
    if (event.target.closest('#search-form')) {
      s.options = [];
      document.querySelectorAll('[data-action="reserve"]').forEach(el => el.disabled = true);
      if (event.target.name === 'date_from') { const end = $('#search-form [name="date_to"]'); end.min = addDays(event.target.value, 1); if (end.value <= event.target.value) end.value = addDays(event.target.value, 2); }
    }
  } catch (error) { toast(error.message); }
});
document.addEventListener('submit', async event => {
  const form = event.target; if (!form.dataset.form) return; event.preventDefault();
  const button = $('button[type="submit"]', form); if (button?.disabled) return;
  if (button) button.disabled = true;
  const data = Object.fromEntries(new FormData(form)); const kind = form.dataset.form;
  const err = $('.form-error', form); if (err) err.textContent = '';
  try {
    if (kind === 'search') { s.search = { ...data, adults: +data.adults, children: +data.children, children_beds: +data.children_beds }; await search(); }
    else if (kind === 'reserve') { const result = await api('/bookings', { method: 'POST', body: { ...s.reserving, ...data, rules_accepted: true } }); closeDialog(); location.hash = 'bookings'; if (s.view === 'bookings') await render(); toast(`Заявка №${result.id} отправлена`); }
    else if (kind === 'admin-search') { s.q = data.q; s.status = data.status; s.offset = 0; await render(); }
    else if (kind === 'receipt') { if (data.file.size > 8 * 1024 * 1024) throw new Error('Максимальный размер чека 8 МБ'); await api(`/bookings/${form.dataset.id}/receipts`, { method: 'POST', body: new FormData(form) }); await detail(form.dataset.id); toast('Чек отправлен на проверку'); }
    else if (kind === 'cancel') { await api(`/bookings/${form.dataset.id}/cancel`, { method: 'POST', body: data }); await detail(form.dataset.id); toast('Запрос отправлен'); }
    else if (kind === 'approve-receipt') { await api(`/admin/receipts/${form.dataset.id}/approve`, { method: 'POST', body: { amount: +data.amount, note: data.note } }); await detail(s.detail.id); toast('Оплата подтверждена'); }
    else if (kind === 'reject-receipt') { await api(`/admin/receipts/${form.dataset.id}/reject`, { method: 'POST', body: data }); await detail(s.detail.id); toast('Чек отклонён'); }
    else if (kind === 'refund') { await api(`/admin/bookings/${form.dataset.id}/refund`, { method: 'POST', body: { amount: +data.amount, note: data.note } }); await detail(form.dataset.id); toast('Возврат учтён'); }
    else if (kind === 'room') { await api(`/admin/rooms/${form.dataset.code}`, { method: 'PATCH', body: { description: data.description, is_available: !!data.is_available } }); s.config = await api('/config'); toast('Объект сохранён'); }
    else if (kind === 'prices') { await api('/admin/prices', { method: 'POST', body: { ...data, price: +data.price } }); await render(); toast('Цены сохранены'); }
    else if (kind === 'season') { await api('/admin/season', { method: 'PUT', body: { year: +data.year, is_active: !!data.is_active } }); s.config = await api('/config'); await render(); toast('Сезон сохранён'); }
    else if (kind === 'block') { await api('/admin/blocks', { method: 'POST', body: data }); await render(); toast('Даты заблокированы'); }
  } catch (error) { if (err?.isConnected) err.textContent = error.message; else toast(error.message); }
  finally { if (button?.isConnected) button.disabled = false; paint(); }
});
window.addEventListener('hashchange', () => { if (location.hash === '#main') { $('#main')?.focus(); return; } closeDialog(); window.scrollTo(0, 0); render(); });
async function boot() {
  s.config = await api('/config');
  let start = addDays(s.config.today, 1); if (+start.slice(0,4) < s.config.booking_year) start = `${s.config.booking_year}-01-02`;
  s.search = { date_from: start, date_to: addDays(start, 2), adults: 2, children: 0, children_beds: 0 };
  const tg = window.Telegram?.WebApp; tg?.ready(); if (tg?.initData) { tg.expand(); const result = await api('/auth/telegram', { method: 'POST', body: { init_data: tg.initData } }); s.token = result.token; sessionStorage.setItem('baikal-token', s.token); }
  if (s.token) { try { s.user = await api('/me'); } catch { s.token = null; } }
  if (s.config.demo && !s.user) { const result = await api('/auth/demo?role=guest', { method: 'POST' }); s.token = result.token; sessionStorage.setItem('baikal-token', s.token); s.user = await api('/me'); }
  await render();
}
boot().catch(error => { $('#app').innerHTML = `<main class="container initial"><h1>Дача на Байкале</h1><p class="error" style="margin-top:25px">${esc(error.message)}</p><button data-action="reload" style="margin-top:20px">Повторить</button></main>`; actions.reload = () => location.reload(); });
