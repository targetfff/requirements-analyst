'use strict';

const state = { mode: 'analyst', busy: false, messages: [] };
let historyRevision = 0; // Ignore a stale initial history fetch after a user action.
const $ = (selector) => document.querySelector(selector);
const messagesEl = $('#messages');
const emptyState = $('#empty-state');
const promptEl = $('#prompt-text');
const submitButton = $('#send-button');
const countEl = $('#char-count');
const errorEl = $('#error-message');
const clearButton = $('#clear-history');

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function showError(message = '') {
  errorEl.hidden = !message;
  errorEl.textContent = message;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Ошибка запроса (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
    } catch { /* fallback */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function formatTime(dateString) {
  // SQLite may return timezone-naive timestamps. The server creates all dates in UTC.
  const iso = /(?:Z|[+-]\d\d:\d\d)$/.test(dateString) ? dateString : `${dateString}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
  });
}

const modeTitles = { analyst: 'Анализ требований', critic: 'Проверка рисков', qa: 'Критерии приёмки' };

function section(title, content) {
  const el = element('section', 'analysis-section');
  el.append(element('h4', '', title), content);
  return el;
}

function list(items) {
  const ul = element('ul', 'analysis-list');
  items.forEach((item) => ul.append(element('li', '', item)));
  return ul;
}

function renderAnalysis(result, mode) {
  const card = element('article', 'analysis-card');
  const top = element('div', 'analysis-top');
  top.append(element('h3', '', modeTitles[mode] || modeTitles.analyst));
  if (result.is_demo === true) {
    top.append(element('span', 'analysis-badge', 'ДЕМО-ШАБЛОН'));
  }
  card.append(top);

  if (result.requirements?.length) {
    const table = element('table', 'requirements-table');
    const body = document.createElement('tbody');
    result.requirements.forEach(({ id, text }) => {
      const tr = document.createElement('tr');
      tr.append(element('td', '', id), element('td', '', text));
      body.append(tr);
    });
    table.append(body);
    card.append(section('Функциональные требования', table));
  }
  if (result.issues?.length) {
    const wrapper = document.createElement('div');
    result.issues.forEach(({ severity, text }) => {
      const item = element('div', 'issue-row');
      item.append(element('span', `severity severity--${severity}`), element('span', '', text));
      wrapper.append(item);
    });
    card.append(section('Замечания и риски', wrapper));
  }
  if (result.acceptance_criteria?.length) {
    card.append(section('Критерии приёмки', list(result.acceptance_criteria)));
  }
  if (result.questions?.length) {
    card.append(section('Вопросы для уточнения', list(result.questions)));
  }
  return card;
}

function renderMessage(message) {
  const item = element('article', `message message--${message.role}`);
  const avatar = element('span', 'message-avatar', message.role === 'user' ? 'ВЫ' : 'RA');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'message-body');
  const meta = element('div', 'message-meta');
  meta.append(element('strong', '', message.role === 'user' ? 'Вы' : 'Requirements Analyst'));
  const time = element('time', '', formatTime(message.created_at));
  time.dateTime = message.created_at;
  meta.append(time);
  body.append(meta, element('div', 'message-text', message.content));
  if (message.role === 'assistant' && message.analysis) {
    body.append(renderAnalysis(message.analysis, message.mode));
  }
  item.append(avatar, body);
  return item;
}

function renderHistory() {
  messagesEl.replaceChildren(...state.messages.map(renderMessage));
  emptyState.hidden = state.messages.length > 0;
  clearButton.disabled = state.busy || state.messages.length === 0;
  $('#conversation').scrollTop = $('#conversation').scrollHeight;
}

function syncComposer() {
  const count = promptEl.value.length;
  const isValid = promptEl.value.trim().length > 0 && count <= 500;
  countEl.textContent = `${count} / 500`;
  submitButton.disabled = !isValid || state.busy;
  promptEl.disabled = state.busy;
  document.querySelectorAll('.mode-btn').forEach((btn) => {
    const active = btn.dataset.mode === state.mode;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
    btn.disabled = state.busy;
    btn.title = { analyst: 'Анализ', critic: 'Критика', qa: 'Критерии приёмки' }[btn.dataset.mode];
  });
}

function setBusy(value) {
  state.busy = value;
  $('#loading-message').hidden = !value;
  clearButton.disabled = value || state.messages.length === 0;
  syncComposer();
}

async function loadHistory() {
  const revision = historyRevision;
  try {
    const loaded = await api('/api/messages');
    if (revision === historyRevision) {
      state.messages = loaded;
      renderHistory();
    }
  } catch (err) {
    showError(`Не удалось получить историю: ${err.message}`);
  }
}

async function sendMessage() {
  const text = promptEl.value.trim();
  if (!text || text.length > 500 || state.busy) return;
  showError();
  historyRevision += 1;
  setBusy(true);
  try {
    const turn = await api('/api/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, mode: state.mode })
    });
    state.messages.push(turn.user, turn.assistant);
    promptEl.value = '';
    renderHistory();
  } catch (err) {
    showError(`Не удалось отправить сообщение: ${err.message}`);
  } finally {
    setBusy(false);
    promptEl.focus();
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('#theme-label').textContent = theme === 'light' ? 'Тёмная тема' : 'Светлая тема';
  $('#theme-icon').textContent = theme === 'light' ? '☾' : '☀';
  try { localStorage.setItem('requirements-theme', theme); } catch { /* storage disabled */ }
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
}

$('#chat-form').addEventListener('submit', (event) => { event.preventDefault(); sendMessage(); });
promptEl.addEventListener('input', syncComposer);
promptEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
});
document.querySelectorAll('.mode-btn').forEach((btn) => btn.addEventListener('click', () => {
  if (!state.busy) { state.mode = btn.dataset.mode; syncComposer(); }
}));
document.querySelectorAll('.example-card').forEach((btn) => btn.addEventListener('click', () => {
  promptEl.value = btn.dataset.example;
  syncComposer();
  promptEl.focus();
}));
$('#theme-toggle').addEventListener('click', toggleTheme);
$('#mobile-theme').addEventListener('click', toggleTheme);
clearButton.addEventListener('click', async () => {
  if (state.busy || !state.messages.length) return;
  if (!window.confirm('Очистить историю единственного диалога?')) return;
  historyRevision += 1;
  try {
    await api('/api/messages', { method: 'DELETE' });
    state.messages = [];
    renderHistory();
    showError();
  } catch (err) { showError(`Не удалось очистить историю: ${err.message}`); }
});

let theme = 'light';
try { theme = localStorage.getItem('requirements-theme') === 'dark' ? 'dark' : 'light'; } catch { /* ignore */ }
applyTheme(theme);
syncComposer();
loadHistory();
