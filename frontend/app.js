'use strict';

const state = { mode: 'business_analyst', authenticated: false, busy: false, loadingHistory: true, messages: [], requirements: null };
let historyRevision = 0; // Ignore a stale initial history fetch after a user action.
let sessionRevision = 0;
let sessionTimer;
let signingIn = false;
const $ = (selector) => document.querySelector(selector);
const appShell = $('.app-shell');
const loginScreen = $('#login-screen');
const loginKey = $('#access-key');
const loginButton = $('#login-button');
const loginError = $('#login-error');
const logoutButton = $('#logout-button');
const messagesEl = $('#messages');
const emptyState = $('#empty-state');
const promptEl = $('#prompt-text');
const submitButton = $('#send-button');
const countEl = $('#char-count');
const errorEl = $('#error-message');
const clearButton = $('#clear-history');
const chatForm = $('#chat-form');
const scrollButton = $('#scroll-to-bottom');
const businessButton = $('#business-analysis');
const systemButton = $('#system-analysis');
const updateRequirements = $('#update-requirements');

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
  const revision = sessionRevision;
  const response = await fetch(path, { cache: 'no-store', credentials: 'same-origin', ...options });
  if (!response.ok) {
    if (response.status === 401 && state.authenticated && revision === sessionRevision) {
      showLogin('Сессия завершилась. Войдите снова.');
    }
    let message = `Ошибка запроса (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
    } catch { /* fallback */ }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function showLoginError(message = '') {
  loginError.textContent = message;
  loginError.hidden = !message;
}

function syncLogin() {
  loginKey.disabled = signingIn;
  loginButton.disabled = signingIn || !loginKey.validity.valid;
}

function showLogin(message = '') {
  clearTimeout(sessionTimer);
  sessionRevision += 1;
  historyRevision += 1;
  state.authenticated = false;
  state.loadingHistory = true;
  state.messages = [];
  state.requirements = null;
  state.mode = 'business_analyst';
  promptEl.value = '';
  updateRequirements.checked = false;
  setBusy(false);
  renderHistory();
  showError();
  appShell.hidden = true;
  loginScreen.hidden = false;
  logoutButton.disabled = false;
  showLoginError(message);
  syncLogin();
  loginKey.focus();
}

function startSession(session) {
  clearTimeout(sessionTimer);
  sessionRevision += 1;
  state.authenticated = true;
  emptyState.hidden = true;
  loginScreen.hidden = true;
  appShell.hidden = false;
  sessionTimer = setTimeout(() => showLogin('Сессия завершилась. Войдите снова.'), session.expires_in * 1000);
  loadHistory();
}

async function restoreSession() {
  try {
    startSession(await api('/api/auth/me'));
  } catch (err) {
    showLogin(err.status === 401 ? '' : `Не удалось проверить сессию: ${err.message}`);
  }
}

async function login(event) {
  event.preventDefault();
  if (signingIn || !loginKey.validity.valid) return;
  const key = loginKey.value;
  loginKey.value = '';
  signingIn = true;
  syncLogin();
  showLoginError();
  try {
    const session = await api('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }),
    });
    startSession(session);
  } catch (err) {
    showLoginError(err.message);
  } finally {
    signingIn = false;
    syncLogin();
    if (!state.authenticated) loginKey.focus();
  }
}

async function logout() {
  const revision = sessionRevision;
  logoutButton.disabled = true;
  try {
    await api('/api/auth/logout', { method: 'POST' });
    if (revision === sessionRevision) showLogin();
  } catch (err) {
    if (revision === sessionRevision) {
      logoutButton.disabled = false;
      showError(`Не удалось выйти: ${err.message}`);
    }
  }
}

function formatTime(dateString) {
  // SQLite may return timezone-naive timestamps. The server creates all dates in UTC.
  const iso = /(?:Z|[+-]\d\d:\d\d)$/.test(dateString) ? dateString : `${dateString}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
  });
}

const modeTitles = {
  business_analyst: 'Business Analyst',
  system_analyst: 'System Analyst',
  clarification: 'Уточнение',
  analyst: 'Анализ требований',
  critic: 'Проверка рисков',
  qa: 'Критерии приёмки',
};

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

function appendListSections(card, sections) {
  for (const [title, items] of sections) {
    if (items?.length) card.append(section(title, list(items)));
  }
}

function renderAnalysis(message) {
  const { analysis: result, mode, requirements_version: version } = message;
  const card = element('article', 'analysis-card');
  const top = element('div', 'analysis-top');
  top.append(element('h3', '', modeTitles[mode] || 'Результат анализа'));
  if (result.is_demo === true) {
    top.append(element('span', 'analysis-badge', 'ДЕМО-ШАБЛОН'));
  }
  if (mode === 'system_analyst') {
    const stale = state.requirements && version !== state.requirements.version;
    if (stale) {
      top.append(element('span', 'analysis-badge analysis-badge--stale', 'Устаревший анализ'));
    } else if (version !== null && version !== undefined) {
      top.append(element('span', 'analysis-badge', `Требования · версия ${version}`));
    }
  }
  card.append(top);

  if (result.feasibility) {
    const { assessment, facts, assumptions } = result.feasibility;
    card.append(section('Реализуемость', element('p', 'analysis-text', assessment)));
    appendListSections(card, [['Факты', facts], ['Допущения', assumptions]]);
  }

  if (result.issues?.length) {
    const wrapper = document.createElement('div');
    result.issues.forEach(({ severity, text }) => {
      const item = element('div', 'issue-row');
      item.append(element('span', `severity severity--${severity}`), element('span', '', text));
      wrapper.append(item);
    });
    card.append(section('Замечания и риски', wrapper));
  } else if (result.feasibility) {
    const summary = element('p', 'analysis-text', 'Замечаний и рисков не выявлено.');
    card.append(section('Замечания и риски', summary));
  }

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

  if (result.architecture) {
    card.append(section('Архитектура', element('p', 'analysis-text', result.architecture)));
  }
  appendListSections(card, [
    ['Критерии приёмки', result.acceptance_criteria],
    ['Минимальный MVP', result.mvp],
    ['Технологии', result.technologies?.map(({ name, rationale }) => `${name} — ${rationale}`)],
    ['Модели данных', result.data_models?.map(({ name, description }) => `${name} — ${description}`)],
    ['API', result.api_endpoints?.map(({ method, path, purpose }) => `${method} ${path} — ${purpose}`)],
    ['Взаимодействие компонентов', result.component_interactions],
    ['Вопросы для уточнения', result.questions],
  ]);
  return card;
}

function renderMessageText({ role, analysis, content }) {
  const text = element('div', 'message-text', content);
  if (role !== 'assistant' || analysis || !window.marked || !window.DOMPurify?.isSupported) return text;

  try {
    const html = window.marked.parse(content, { breaks: true });
    const fragment = window.DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        'a', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
        'li', 'ol', 'p', 'pre', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
      ],
      ALLOWED_ATTR: ['href', 'start', 'title'],
      ALLOW_ARIA_ATTR: false,
      ALLOW_DATA_ATTR: false,
      RETURN_DOM_FRAGMENT: true,
    });
    text.replaceChildren(fragment);
    text.classList.add('message-markdown');
  } catch {
    // Keep the original text when parsing or sanitization fails.
  }
  return text;
}

function renderMessage(message) {
  const item = element('article', `message message--${message.role}`);
  if (message.mode === 'clarification') item.classList.add('message--clarification');
  const avatar = element('span', 'message-avatar', message.role === 'user' ? 'ВЫ' : 'RA');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'message-body');
  const meta = element('div', 'message-meta');
  meta.append(element('strong', '', message.role === 'user' ? 'Вы' : 'Requirements Analyst'));
  const time = element('time', '', formatTime(message.created_at));
  time.dateTime = message.created_at;
  meta.append(time);
  body.append(meta, renderMessageText(message));
  if (message.role === 'assistant' && message.analysis) {
    body.append(renderAnalysis(message));
  }
  item.append(avatar, body);
  return item;
}

function renderRequirements() {
  const current = state.requirements;
  $('#requirements-toolbar').hidden = !current;
  businessButton.hidden = !current || current.version === 1 || state.messages.some(message =>
    message.mode === 'business_analyst' && message.analysis &&
    message.requirements_version === current.version
  );
  if (!current) return;

  $('#requirements-version').textContent = `Актуальные требования · версия ${current.version}`;
  const documentEl = $('#requirements-document');
  documentEl.replaceChildren(list(current.requirements.map(({ id, text }) => `${id} — ${text}`)));
  if (!current.requirements.length) documentEl.textContent = 'Список требований пуст.';
  appendListSections(documentEl, [
    ['Критерии приёмки', current.acceptance_criteria],
    ['Минимальный MVP', current.mvp],
  ]);

  const analyses = state.messages.filter(message => message.mode === 'system_analyst' && message.analysis);
  systemButton.hidden = !current.requirements.length ||
    analyses.some(message => message.requirements_version === current.version);
  systemButton.textContent = analyses.length ? 'Повторить системный анализ' : 'Перейти к системному анализу';
}

function renderHistory() {
  messagesEl.replaceChildren(...state.messages.map(renderMessage));
  emptyState.hidden = state.messages.length > 0;
  clearButton.disabled = state.busy || state.messages.length === 0;
  renderRequirements();
  syncScrollButton();
}

function scrollToLatestMessage(behavior = 'auto') {
  window.scrollTo({ top: document.scrollingElement.scrollHeight, behavior });
}

function syncScrollButton() {
  const { scrollHeight, scrollTop, clientHeight } = document.scrollingElement;
  const visible = state.messages.length > 0 && scrollHeight - scrollTop - clientHeight > 80;
  scrollButton.classList.toggle('is-visible', visible);
  scrollButton.inert = !visible;
}

function syncComposer() {
  const count = promptEl.value.length;
  const isValid = promptEl.value.trim().length > 0 && count <= 500;
  countEl.textContent = `${count} / 500`;
  submitButton.disabled = !isValid || state.busy || state.loadingHistory;
  promptEl.disabled = state.busy;
  businessButton.disabled = state.busy || state.loadingHistory;
  systemButton.disabled = state.busy;
  $('#requirements-edit').hidden = state.mode !== 'clarification' || !state.requirements;
  updateRequirements.disabled = state.busy;
  document.querySelectorAll('.mode-btn').forEach((btn) => {
    const active = btn.dataset.mode === state.mode;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
    btn.disabled = state.busy;
  });
}

function setBusy(value) {
  state.busy = value;
  $('#loading-message').hidden = !value;
  clearButton.disabled = value || state.messages.length === 0;
  syncComposer();
}

async function loadHistory() {
  const session = sessionRevision;
  const revision = historyRevision;
  try {
    const loaded = await api('/api/chat');
    if (revision === historyRevision) {
      state.messages = loaded.messages;
      state.requirements = loaded.requirements;
      if (state.loadingHistory && state.requirements && state.mode === 'business_analyst') {
        state.mode = 'clarification';
      }
      renderHistory();
    }
  } catch (err) {
    if (session === sessionRevision) showError(`Не удалось получить историю: ${err.message}`);
  } finally {
    if (session === sessionRevision) {
      const showLatest = state.loadingHistory && state.messages.length > 0;
      state.loadingHistory = false;
      syncComposer();
      if (showLatest) scrollToLatestMessage();
    }
  }
}

async function submitTurn(payload, clearComposer = false) {
  if (!state.authenticated || state.busy || state.loadingHistory) return;
  const session = sessionRevision;
  showError();
  historyRevision += 1;
  setBusy(true);
  try {
    const turn = await api('/api/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (session !== sessionRevision) return;
    const initialAnalysis = !state.requirements && turn.requirements && payload.mode === 'business_analyst';
    state.messages.push(turn.user, turn.assistant);
    state.requirements = turn.requirements;
    if (initialAnalysis) state.mode = 'clarification';
    if (clearComposer) {
      promptEl.value = '';
      updateRequirements.checked = turn.requires_edit_clarification === true;
    }
    renderHistory();
  } catch (err) {
    if (session === sessionRevision && err.status === 409) await loadHistory();
    if (session === sessionRevision) showError(`Не удалось отправить сообщение: ${err.message}`);
  } finally {
    if (session === sessionRevision) {
      setBusy(false);
      promptEl.focus({ preventScroll: true });
      scrollToLatestMessage();
    }
  }
}

function sendMessage() {
  const text = promptEl.value.trim();
  if (!text || text.length > 500) return;
  const payload = { text, mode: state.mode };
  if (state.mode === 'clarification' && updateRequirements.checked) payload.update_requirements = true;
  return submitTurn(payload, true);
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

chatForm.addEventListener('submit', (event) => { event.preventDefault(); sendMessage(); });
$('#login-form').addEventListener('submit', login);
loginKey.addEventListener('input', () => { showLoginError(); syncLogin(); });
logoutButton.addEventListener('click', logout);
scrollButton.addEventListener('click', () => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  scrollToLatestMessage(reduceMotion ? 'auto' : 'smooth');
  chatForm.focus({ preventScroll: true });
});
window.addEventListener('scroll', syncScrollButton, { passive: true });
window.addEventListener('resize', syncScrollButton);
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
businessButton.addEventListener('click', () => submitTurn({
  mode: 'business_analyst', text: 'Повтори бизнес-анализ с учётом актуальных требований.',
}));
systemButton.addEventListener('click', () => submitTurn({ mode: 'system_analyst' }));
clearButton.addEventListener('click', async () => {
  if (state.busy || !state.messages.length) return;
  if (!window.confirm('Очистить историю диалога и все версии требований?')) return;
  historyRevision += 1;
  const session = sessionRevision;
  setBusy(true);
  try {
    await api('/api/messages', { method: 'DELETE' });
    if (session !== sessionRevision) return;
    state.messages = [];
    state.requirements = null;
    state.mode = 'business_analyst';
    updateRequirements.checked = false;
    renderHistory();
    showError();
  } catch (err) {
    if (session === sessionRevision) showError(`Не удалось очистить историю: ${err.message}`);
  } finally {
    if (session === sessionRevision) setBusy(false);
  }
});

let theme = 'light';
try { theme = localStorage.getItem('requirements-theme') === 'dark' ? 'dark' : 'light'; } catch { /* ignore */ }
applyTheme(theme);
syncComposer();
restoreSession();
