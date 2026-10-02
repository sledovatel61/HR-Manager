/* HR Manager — lightweight interactions for the static Mission Control concept. */
const iconPaths = {
  inbox: '<path d="M4 4.5h16v12H15l-2 3h-2l-2-3H4z"/><path d="M4 12h4l1.5 2h5L16 12h4"/>',
  users: '<path d="M16 20v-1.6a3.4 3.4 0 0 0-3.4-3.4H6.4A3.4 3.4 0 0 0 3 18.4V20"/><circle cx="9.5" cy="7" r="3.5"/><path d="M17 11a3.4 3.4 0 0 0 0-6.6M21 20v-1.6a3.4 3.4 0 0 0-2.5-3.3"/>',
  kanban: '<rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M9.2 4v16M15 4v10"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M7.5 3v4M16.5 3v4M3.5 9.5h17M7 13h3m-3 3.5h3m3-3.5h4m-4 3.5h4"/>',
  briefcase: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5.5A1.5 1.5 0 0 1 9.5 4h5A1.5 1.5 0 0 1 16 5.5V7M3 12h18m-11-1v2h4v-2"/>',
  chart: '<path d="M4 19V5m0 14h17"/><path d="m7 15 4-4 3 2 6-7"/><path d="M16.5 6H20v3.5"/>',
  bell: '<path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  file: '<path d="M6 3.5h8l4.5 4.5v12.5H6z"/><path d="M14 3.5V8h4.5M9 12h6m-6 3.5h6"/>',
  folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h5l2 2h8A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/><path d="M3.5 9h17"/>',
  trash: '<path d="M4 7h16m-10-3h4m-8 3 1 13h10l1-13M10 11v5m4-5v5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1a1.8 1.8 0 1 1-2.5 2.5l-.1-.1a1.8 1.8 0 0 0-3 .9v.2a1.8 1.8 0 1 1-3.6 0v-.2a1.8 1.8 0 0 0-3-.9l-.1.1a1.8 1.8 0 1 1-2.5-2.5l.1-.1a1.8 1.8 0 0 0-.9-3h-.2a1.8 1.8 0 1 1 0-3.6h.2a1.8 1.8 0 0 0 .9-3l-.1-.1a1.8 1.8 0 1 1 2.5-2.5l.1.1a1.8 1.8 0 0 0 3-.9v-.2a1.8 1.8 0 1 1 3.6 0v.2a1.8 1.8 0 0 0 3 .9l.1-.1a1.8 1.8 0 1 1 2.5 2.5l-.1.1a1.8 1.8 0 0 0 .9 3h.2a1.8 1.8 0 1 1 0 3.6h-.2a1.8 1.8 0 0 0-.9 3Z"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.7"/><path d="m16 16 4.2 4.2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  'arrow-up-right': '<path d="M7 17 17 7M8 7h9v9"/>',
  'arrow-down-right': '<path d="M7 7 17 17M8 17h9V8"/>',
  'arrow-right': '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  'arrow-left': '<path d="M20 12H5m6 6-6-6 6-6"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  'chevron-left': '<path d="m15 18-6-6 6-6"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  check: '<path d="m5 12 4.2 4.2L19 6.5"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16.5 8"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  moon: '<path d="M20.5 14.4A8.3 8.3 0 0 1 9.6 3.5a8.6 8.6 0 1 0 10.9 10.9Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  density: '<path d="M4 6h16M4 12h16M4 18h16"/><path d="M8 4v4m8 2v4m-8 2v4"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 20h16"/>',
  upload: '<path d="M12 16V4m-5 5 5-5 5 5M4 20h16"/>',
  sliders: '<path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  edit: '<path d="m14 5 5 5M4 20l4.5-.8L19 8.7 15.3 5 4.8 15.5 4 20Z"/><path d="M12 20h8"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/>',
  phone: '<path d="M7.3 3.5H5a2 2 0 0 0-2 2.2 16.8 16.8 0 0 0 15.8 15.2 2 2 0 0 0 2.2-2v-2.3l-4.4-2-2 2.4a13.1 13.1 0 0 1-6.1-6.1l2.4-2Z"/>',
  location: '<path d="M19 10.3c0 5-7 10.2-7 10.2S5 15.3 5 10.3a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.3"/>',
  message: '<path d="M20 11.3a7.8 7.8 0 0 1-8 7.7 8.6 8.6 0 0 1-3.4-.7L4 20l1.2-4.2A7.3 7.3 0 0 1 4 11.3a7.8 7.8 0 0 1 8-7.8 7.8 7.8 0 0 1 8 7.8Z"/><path d="M8 11h8m-8 3h5"/>',
  external: '<path d="M13 5h6v6M19 5l-9 9"/><path d="M18 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.1 0l2-2A5 5 0 0 0 12 3.9l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1 0l-2 2A5 5 0 0 0 12 20.1l1.1-1.1"/>',
  paperclip: '<path d="m8.5 12.5 6.9-6.9a3.1 3.1 0 0 1 4.4 4.4l-9.2 9.2a5 5 0 0 1-7.1-7.1l9-9"/><path d="m8.3 14.2 6.3-6.3"/>',
  'calendar-plus': '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M7.5 3v4m9-4v4m-13 2.5h17M12 12v5m-2.5-2.5h5"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
  bookmark: '<path d="M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21l-6-4-6 4Z"/>',
  sparkle: '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z"/><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
  grip: '<circle cx="8" cy="6" r=".8"/><circle cx="16" cy="6" r=".8"/><circle cx="8" cy="12" r=".8"/><circle cx="16" cy="12" r=".8"/><circle cx="8" cy="18" r=".8"/><circle cx="16" cy="18" r=".8"/>'
};

function icon(name, className = "") {
  const paths = iconPaths[name] || iconPaths.info;
  return `<svg class="icon ${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

function renderIcons(scope = document) {
  scope.querySelectorAll("[data-icon]").forEach((node) => {
    if (node.dataset.iconRendered === "true") return;
    node.innerHTML = icon(node.dataset.icon);
    node.dataset.iconRendered = "true";
  });
}

renderIcons();

const root = document.documentElement;
const appShell = document.querySelector(".app-shell");
const sidebar = document.getElementById("app-sidebar");
const toastRegion = document.getElementById("toast-region");
const commandDialog = document.getElementById("command-palette");
const commandInput = document.getElementById("command-input");
const commandResults = document.getElementById("command-results");
const candidateModal = document.getElementById("candidate-modal");
let toastTimer = null;

function readPreference(key, fallback) {
  try {
    return localStorage.getItem(`hr-mission-control-${key}`) || fallback;
  } catch {
    return fallback;
  }
}
function savePreference(key, value) {
  try { localStorage.setItem(`hr-mission-control-${key}`, value); } catch { /* preview still works without storage */ }
}

function applyTheme(theme, persist = true) {
  const next = theme === "light" ? "light" : "dark";
  root.dataset.theme = next;
  const button = document.getElementById("theme-toggle");
  if (button) {
    button.setAttribute("aria-pressed", String(next === "dark"));
    button.title = next === "dark" ? "Переключить на светлую тему" : "Переключить на тёмную тему";
    button.setAttribute("aria-label", button.title);
    const iconNode = button.querySelector("[data-icon]");
    if (iconNode) {
      iconNode.dataset.icon = next === "dark" ? "moon" : "sun";
      iconNode.dataset.iconRendered = "false";
      renderIcons(button);
    }
    const label = button.querySelector(".preference-label");
    if (label) label.textContent = next === "dark" ? "Тёмная" : "Светлая";
  }
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.content = next === "dark" ? "#090e14" : "#eef3f5";
  if (persist) savePreference("theme", next);
}

function applyDensity(density, persist = true) {
  const next = density === "compact" ? "compact" : "comfortable";
  root.dataset.density = next;
  const button = document.getElementById("density-toggle");
  if (button) {
    button.setAttribute("aria-pressed", String(next === "compact"));
    button.title = next === "compact" ? "Плотность: компактная. Переключить на обычную" : "Плотность: обычная. Переключить на компактную";
    const label = button.querySelector(".preference-label");
    if (label) label.textContent = next === "compact" ? "Компактно" : "Обычная";
  }
  if (persist) savePreference("density", next);
}

function applyFonts(mode, persist = true) {
  const next = mode === "system" ? "system" : "display";
  root.dataset.fonts = next;
  const button = document.getElementById("font-toggle");
  if (button) {
    button.setAttribute("aria-pressed", String(next === "system"));
    button.title = next === "system" ? "Системные шрифты включены. Нажмите, чтобы вернуться к DM Sans" : "Переключить на системные шрифты";
    const label = button.querySelector(".preference-label");
    if (label) label.textContent = next === "system" ? "Системные" : "DM Sans";
  }
  if (persist) savePreference("fonts", next);
}

applyTheme(readPreference("theme", "dark"), false);
applyDensity(readPreference("density", "comfortable"), false);
applyFonts(readPreference("fonts", "display"), false);

document.getElementById("theme-toggle").addEventListener("click", () => applyTheme(root.dataset.theme === "dark" ? "light" : "dark"));
document.getElementById("density-toggle").addEventListener("click", () => applyDensity(root.dataset.density === "compact" ? "comfortable" : "compact"));
document.getElementById("font-toggle").addEventListener("click", () => applyFonts(root.dataset.fonts === "system" ? "display" : "system"));

document.getElementById("sidebar-collapse").addEventListener("click", () => {
  appShell.classList.toggle("is-collapsed");
  const isCollapsed = appShell.classList.contains("is-collapsed");
  const button = document.getElementById("sidebar-collapse");
  button.setAttribute("aria-label", isCollapsed ? "Развернуть меню" : "Свернуть меню");
  button.title = isCollapsed ? "Развернуть меню" : "Свернуть меню";
});

const sidebarScrim = document.createElement("button");
sidebarScrim.type = "button";
sidebarScrim.className = "sidebar-scrim";
sidebarScrim.setAttribute("aria-label", "Закрыть меню");
sidebarScrim.disabled = true;
document.body.appendChild(sidebarScrim);
const mobileMenuButton = document.getElementById("mobile-menu");
const mobileSidebarQuery = window.matchMedia("(max-width: 840px)");
function syncSidebarAccessibility() {
  const isMobile = mobileSidebarQuery.matches;
  const isOpen = sidebar.classList.contains("is-open");
  sidebar.inert = isMobile && !isOpen;
  sidebar.setAttribute("aria-hidden", String(isMobile && !isOpen));
  mobileMenuButton.setAttribute("aria-expanded", String(isOpen));
  mobileMenuButton.setAttribute("aria-label", isOpen ? "Закрыть меню" : "Открыть меню");
  sidebarScrim.disabled = !isMobile || !isOpen;
  sidebarScrim.setAttribute("aria-hidden", String(!isMobile || !isOpen));
}
function closeMobileSidebar() {
  sidebar.classList.remove("is-open");
  sidebarScrim.classList.remove("is-visible");
  syncSidebarAccessibility();
}
mobileMenuButton.addEventListener("click", () => {
  sidebar.classList.toggle("is-open");
  sidebarScrim.classList.toggle("is-visible", sidebar.classList.contains("is-open"));
  syncSidebarAccessibility();
});
mobileSidebarQuery.addEventListener("change", syncSidebarAccessibility);
syncSidebarAccessibility();
sidebarScrim.addEventListener("click", closeMobileSidebar);
sidebar.querySelectorAll(".nav-link, .brand").forEach((link) => link.addEventListener("click", closeMobileSidebar));

const pageLabels = {
  dashboard: "Моя очередь",
  candidates: "Кандидаты",
  pipeline: "Воронка",
  analytics: "Аналитика",
  "new-list": "Новый список",
  calendar: "Календарь",
  schedule: "График выхода",
  notifications: "Уведомления",
  reminders: "Напоминания",
  documents: "Списки документов",
  library: "Шаблоны и материалы",
  trash: "Удалённые",
  settings: "Настройки"
};
const sectionDescriptions = {
  calendar: "Все встречи, интервью и решения команды — в одном ритме.",
  schedule: "Планируйте выходы новых сотрудников и следите за готовностью документов.",
  notifications: "Важные изменения по кандидатам и задачам команды.",
  reminders: "Задачи, которые помогают ни о чём не забыть.",
  documents: "Списки и статусы документов для каждого этапа найма.",
  library: "Шаблоны, материалы и документы команды в одном месте.",
  trash: "Удалённые записи и восстановление данных.",
  settings: "Пользователи, интеграции и параметры рабочего пространства."
};
const supportedPages = new Set(["dashboard", "candidates", "pipeline", "analytics", "new-list"]);
function renderRoute() {
  const route = decodeURIComponent(window.location.hash.replace(/^#/, "")) || "dashboard";
  const page = supportedPages.has(route) ? route : "coming-soon";
  document.querySelectorAll("[data-page]").forEach((node) => {
    node.hidden = node.dataset.page !== page;
  });
  const sectionName = pageLabels[route] || "Моя очередь";
  document.getElementById("current-section").textContent = sectionName;
  document.title = `${sectionName} — HR Manager`;
  const navRoute = route === "new-list" ? "documents" : route;
  document.querySelectorAll("[data-route]").forEach((link) => {
    const active = link.dataset.route === navRoute;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (page === "coming-soon") {
    const title = pageLabels[route] || "Раздел";
    const heading = document.getElementById("coming-title");
    if (heading.firstChild) heading.firstChild.nodeValue = title;
    document.getElementById("coming-description").textContent = sectionDescriptions[route] || "Продолжайте работу в едином пространстве команды.";
  }
  closeMobileSidebar();
}
window.addEventListener("hashchange", renderRoute);
renderRoute();

function showToast(message) {
  if (!message) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.setAttribute("role", "status");
  const iconWrap = document.createElement("span");
  iconWrap.className = "toast-icon";
  iconWrap.innerHTML = icon("check");
  const text = document.createElement("span");
  text.className = "toast-message";
  text.textContent = message;
  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("aria-label", "Закрыть уведомление");
  close.innerHTML = icon("close");
  close.addEventListener("click", () => removeToast(toast));
  toast.append(iconWrap, text, close);
  toastRegion.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  const timer = window.setTimeout(() => removeToast(toast), 3600);
  toast.addEventListener("mouseenter", () => window.clearTimeout(timer), { once: true });
}
function removeToast(toast) {
  if (!toast.isConnected) return;
  toast.classList.remove("is-visible");
  window.setTimeout(() => toast.remove(), 220);
}

document.addEventListener("click", (event) => {
  const toastButton = event.target.closest("[data-toast]");
  if (toastButton) showToast(toastButton.dataset.toast);
  const routeButton = event.target.closest("[data-route-shortcut]");
  if (routeButton) window.location.hash = routeButton.dataset.routeShortcut;
  if (event.target.closest("[data-open-candidate-modal]")) openCandidateModal();
  if (event.target.closest("[data-close-candidate-modal]")) candidateModal.close();
});

// Command palette: Ctrl/Cmd+K, or the search affordance in the top bar.
const routeCommands = [
  { title: "Моя очередь", detail: "Главный экран · задачи и встречи", route: "dashboard", icon: "inbox" },
  { title: "Кандидаты", detail: "Профили, документы и сообщения", route: "candidates", icon: "users" },
  { title: "Воронка", detail: "Этапы и активные кандидаты", route: "pipeline", icon: "kanban" },
  { title: "Аналитика", detail: "Конверсия, скорость и источники", route: "analytics", icon: "chart" },
  { title: "Создать список документов", detail: "Мастер настройки · два шага", route: "new-list", icon: "file" },
  { title: "Календарь", detail: "События и интервью команды", route: "calendar", icon: "calendar" },
  { title: "График выхода", detail: "План выходов сотрудников", route: "schedule", icon: "briefcase" },
  { title: "Уведомления", detail: "Обновления рабочего пространства", route: "notifications", icon: "bell" },
  { title: "Шаблоны и материалы", detail: "Библиотека команды", route: "library", icon: "folder" },
  { title: "Настройки", detail: "Пользователи и интеграции", route: "settings", icon: "settings" }
];
let commandCandidates = [];
let commandSelection = null;
function refreshCommandCandidates() {
  commandCandidates = Array.from(document.querySelectorAll("[data-candidate-row]")).map((row) => ({
    title: row.dataset.name,
    detail: `${row.dataset.role} · ${row.dataset.stage}`,
    route: "candidates",
    icon: "users",
    candidateName: row.dataset.name
  }));
}
function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}
function commandButton(item, index) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "command-result";
  button.setAttribute("role", "option");
  button.dataset.commandIndex = String(index);
  button.dataset.commandRoute = item.route;
  if (item.candidateName) button.dataset.commandCandidate = item.candidateName;
  button.innerHTML = `<span class="command-result-icon">${icon(item.icon)}</span><span class="command-result-copy"><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.detail)}</small></span>${icon("arrow-up-right")}`;
  return button;
}
function renderCommandResults(query = "") {
  const needle = query.trim().toLocaleLowerCase("ru");
  commandResults.replaceChildren();
  const routes = routeCommands.filter((item) => `${item.title} ${item.detail}`.toLocaleLowerCase("ru").includes(needle));
  const people = commandCandidates.filter((item) => `${item.title} ${item.detail}`.toLocaleLowerCase("ru").includes(needle)).slice(0, 7);
  let index = 0;
  if (routes.length) {
    const label = document.createElement("div");
    label.className = "command-group-label";
    label.textContent = "Разделы и действия";
    commandResults.appendChild(label);
    routes.slice(0, needle ? 6 : 5).forEach((item) => commandResults.appendChild(commandButton(item, index++)));
  }
  if (people.length) {
    const label = document.createElement("div");
    label.className = "command-group-label";
    label.textContent = "Кандидаты";
    commandResults.appendChild(label);
    people.forEach((item) => commandResults.appendChild(commandButton(item, index++)));
  }
  if (!routes.length && !people.length) {
    const empty = document.createElement("div");
    empty.className = "command-empty";
    empty.textContent = "Ничего не найдено. Попробуйте другое имя или раздел.";
    commandResults.appendChild(empty);
  }
  commandSelection = null;
}
function openCommandPalette() {
  if (!commandDialog.open) commandDialog.showModal();
  commandInput.value = "";
  refreshCommandCandidates();
  renderCommandResults();
  window.setTimeout(() => commandInput.focus(), 20);
}
document.getElementById("open-command").addEventListener("click", openCommandPalette);
commandInput.addEventListener("input", () => renderCommandResults(commandInput.value));
commandDialog.addEventListener("click", (event) => {
  const result = event.target.closest("[data-command-route]");
  if (!result) return;
  const candidateName = result.dataset.commandCandidate;
  const route = result.dataset.commandRoute;
  commandDialog.close();
  window.location.hash = route;
  if (candidateName) window.setTimeout(() => selectCandidateByName(candidateName), 80);
});
commandDialog.addEventListener("keydown", (event) => {
  const buttons = Array.from(commandResults.querySelectorAll(".command-result"));
  if (!buttons.length) return;
  const active = document.activeElement.closest?.(".command-result");
  let index = active ? buttons.indexOf(active) : -1;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    index = (index + 1) % buttons.length;
    buttons[index].focus();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    index = index <= 0 ? buttons.length - 1 : index - 1;
    buttons[index].focus();
  } else if (event.key === "Enter" && document.activeElement === commandInput) {
    event.preventDefault();
    buttons[0].click();
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openCommandPalette();
  }
  if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey) {
    const target = event.target;
    const isTyping = target.matches("input, textarea, select, [contenteditable='true']");
    if (!isTyping && !commandDialog.open && !candidateModal.open && !document.querySelector("dialog[open]")) {
      const search = document.getElementById("candidate-search");
      if (!document.querySelector('[data-page="candidates"]')?.hidden) {
        event.preventDefault();
        search.focus();
      } else openCommandPalette();
    }
  }
});

// Candidate table filtering and profile drawer.
const candidateSearch = document.getElementById("candidate-search");
const stageFilter = document.getElementById("candidate-stage-filter");
const candidateLayout = document.getElementById("candidate-layout");
const candidateEmpty = document.getElementById("candidate-empty");
function filterCandidates() {
  const query = (candidateSearch?.value || "").trim().toLocaleLowerCase("ru");
  const stage = stageFilter?.value || "";
  const rows = Array.from(document.querySelectorAll("[data-candidate-row]"));
  let visible = 0;
  rows.forEach((row) => {
    const matchesText = !query || row.textContent.toLocaleLowerCase("ru").includes(query);
    const matchesStage = !stage || row.dataset.stage === stage;
    row.hidden = !(matchesText && matchesStage);
    if (!row.hidden) visible += 1;
  });
  document.getElementById("visible-candidate-count").textContent = String(visible);
  document.getElementById("table-range").textContent = String(visible);
  const plural = visible === 1 ? "кандидат" : visible > 1 && visible < 5 ? "кандидата" : "кандидатов";
  document.querySelector(".table-result-count").innerHTML = `<strong>${visible}</strong> ${plural}`;
  candidateEmpty.hidden = visible !== 0;
  document.querySelector(".candidate-table").hidden = visible === 0;
}
candidateSearch.addEventListener("input", filterCandidates);
stageFilter.addEventListener("change", filterCandidates);

const stageClassMap = { "Собеседование": "info", "Отклик": "neutral", "Оффер": "success", "Звонок": "warning", "Новая заявка": "violet", "Выход": "teal" };
const toneClassMap = { teal: "teal", blue: "blue", rose: "rose", violet: "violet", amber: "amber", lilac: "lilac" };
function openDrawer() {
  candidateLayout.classList.remove("drawer-collapsed");
  document.getElementById("candidate-drawer").setAttribute("aria-hidden", "false");
}
function selectCandidate(row) {
  if (!row) return;
  document.querySelectorAll("[data-candidate-row]").forEach((item) => item.classList.toggle("is-selected", item === row));
  const name = row.dataset.name || "Кандидат";
  const role = row.dataset.role || "Вакансия не указана";
  const stage = row.dataset.stage || "Отклик";
  const initials = row.dataset.initials || name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toLocaleUpperCase("ru");
  const tone = toneClassMap[row.dataset.tone] || "teal";
  const stageClass = stageClassMap[stage] || row.dataset.stageClass || "neutral";
  document.getElementById("drawer-name").textContent = name;
  document.getElementById("drawer-role").textContent = role;
  document.getElementById("drawer-avatar").textContent = initials;
  document.getElementById("drawer-avatar").className = `avatar avatar-large avatar-${tone}`;
  const stageChip = document.getElementById("drawer-stage");
  stageChip.className = `status-chip status-${stageClass}`;
  stageChip.innerHTML = `<i></i><span>${escapeHTML(stage)}</span>`;
  document.getElementById("drawer-date").textContent = row.dataset.date || "02.10.2026";
  const emailText = row.querySelector(".candidate-person small")?.textContent || "Контакт не указан";
  const emailLink = document.getElementById("drawer-email");
  emailLink.textContent = emailText;
  emailLink.href = emailText.includes("@") ? `mailto:${emailText}` : "#";
  openDrawer();
}
function selectCandidateByName(name) {
  const row = Array.from(document.querySelectorAll("[data-candidate-row]")).find((item) => item.dataset.name === name);
  if (row) selectCandidate(row);
}
document.querySelectorAll("[data-candidate-row]").forEach((row) => {
  const button = row.querySelector("[data-candidate-open]");
  button.addEventListener("click", () => selectCandidate(row));
  row.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    selectCandidate(row);
  });
});
document.getElementById("close-candidate-drawer").addEventListener("click", () => {
  candidateLayout.classList.add("drawer-collapsed");
  document.getElementById("candidate-drawer").setAttribute("aria-hidden", "true");
});
document.querySelectorAll("[data-drawer-tab]").forEach((button) => {
  button.tabIndex = button.classList.contains("is-active") ? 0 : -1;
  button.addEventListener("click", () => {
    const tab = button.dataset.drawerTab;
    document.querySelectorAll("[data-drawer-tab]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-drawer-panel]").forEach((panel) => { panel.hidden = panel.dataset.drawerPanel !== tab; });
  });
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const tabs = Array.from(document.querySelectorAll("[data-drawer-tab]"));
    const index = tabs.indexOf(button);
    const direction = event.key === "ArrowRight" ? 1 : -1;
    tabs[(index + direction + tabs.length) % tabs.length].focus();
    tabs[(index + direction + tabs.length) % tabs.length].click();
  });
});
document.getElementById("candidate-search").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { candidateSearch.value = ""; filterCandidates(); candidateSearch.blur(); }
});

// Add a realistic demo candidate to the table without ever injecting user HTML.
function appendCandidateRow(name, role) {
  const initials = name.trim().split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toLocaleUpperCase("ru");
  const date = "02.10.2026";
  const row = document.createElement("tr");
  row.className = "candidate-row";
  Object.assign(row.dataset, { candidateRow: "", name, role, stage: "Новая заявка", stageClass: "violet", date, initials, tone: "teal" });
  const personCell = document.createElement("td");
  const personButton = document.createElement("button");
  personButton.type = "button";
  personButton.className = "candidate-person";
  personButton.dataset.candidateOpen = "";
  personButton.setAttribute("aria-label", `Открыть карточку ${name}`);
  const avatar = document.createElement("span");
  avatar.className = "avatar avatar-table avatar-teal";
  avatar.textContent = initials;
  const personCopy = document.createElement("span");
  const personName = document.createElement("strong");
  personName.textContent = name;
  const email = document.createElement("small");
  email.textContent = "Новый профиль · email не указан";
  personCopy.append(personName, email);
  personButton.append(avatar, personCopy);
  personCell.appendChild(personButton);
  const stageCell = document.createElement("td");
  stageCell.innerHTML = '<span class="status-chip status-violet"><i></i>Новая заявка</span>';
  const roleCell = document.createElement("td"); roleCell.textContent = role;
  const dateCell = document.createElement("td"); dateCell.className = "table-date mono"; dateCell.textContent = date;
  const actionCell = document.createElement("td");
  const actionButton = document.createElement("button");
  actionButton.type = "button"; actionButton.className = "row-more icon-button"; actionButton.setAttribute("aria-label", `Действия для ${name}`); actionButton.dataset.toast = "Действия с кандидатом"; actionButton.innerHTML = icon("more");
  actionCell.appendChild(actionButton);
  row.append(personCell, stageCell, roleCell, dateCell, actionCell);
  document.getElementById("candidate-rows").prepend(row);
  const openButton = row.querySelector("[data-candidate-open]");
  openButton.addEventListener("click", () => selectCandidate(row));
  row.addEventListener("click", (event) => { if (!event.target.closest("button")) selectCandidate(row); });
  return row;
}
function openCandidateModal() {
  if (!candidateModal.open) candidateModal.showModal();
  window.setTimeout(() => document.getElementById("new-candidate-name").focus(), 20);
}

document.getElementById("new-candidate-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const nameInput = document.getElementById("new-candidate-name");
  const roleSelect = document.getElementById("new-candidate-role");
  const name = nameInput.value.trim();
  if (!name || !roleSelect.value) {
    if (!name) nameInput.reportValidity();
    else roleSelect.reportValidity();
    return;
  }
  const row = appendCandidateRow(name, roleSelect.value);
  candidateModal.close();
  event.currentTarget.reset();
  window.location.hash = "candidates";
  window.setTimeout(() => {
    filterCandidates();
    selectCandidate(row);
    showToast(`Профиль «${name}» добавлен в базу`);
  }, 80);
});

// Queue task completion feedback.
document.querySelectorAll("[data-complete-task]").forEach((button) => {
  button.addEventListener("click", () => {
    const task = button.closest("[data-task-item]");
    const isCompleted = task.classList.toggle("is-completed");
    button.setAttribute("aria-label", isCompleted ? "Вернуть задачу в очередь" : "Отметить задачу выполненной");
    const label = button.querySelector(".task-action-label");
    if (label) label.textContent = isCompleted ? "Готово" : "Готово";
    showToast(isCompleted ? "Задача отмечена выполненной" : "Задача возвращена в очередь");
  });
});

// Small period controls update the visible summary and chart.
const chartRanges = {
  "7д": { total: "118", subtitle: "Активность по неделям", path: "M46 145 C88 136 102 121 145 129 S205 112 245 117 S306 85 345 99 S407 91 446 76 S510 85 546 66 S610 72 644 50 S707 50 742 31" },
  "30д": { total: "486", subtitle: "Активность за последние 30 дней", path: "M46 154 C91 146 105 123 145 132 S200 112 245 121 S304 96 345 105 S405 72 446 87 S505 63 546 72 S605 52 644 65 S705 32 742 26" }
};
document.querySelectorAll("[data-chart-range]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-chart-range]").forEach((item) => item.classList.toggle("is-selected", item === button));
    const range = chartRanges[button.dataset.chartRange];
    if (!range) return;
    document.getElementById("chart-total").textContent = range.total;
    document.querySelector(".chart-panel .panel-heading p").textContent = range.subtitle;
    document.getElementById("activity-line").setAttribute("d", range.path);
  });
});
const analyticsPeriods = document.querySelectorAll("[data-analytics-period]");
analyticsPeriods.forEach((button) => {
  button.setAttribute("aria-pressed", String(button.classList.contains("is-selected")));
  button.addEventListener("click", () => {
    analyticsPeriods.forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-selected", active);
      item.setAttribute("aria-pressed", String(active));
    });
    const rangeButton = document.querySelector(".date-range-button");
    const labels = {
      День: "02.10.2026",
      Неделя: "28.09.2026 — 04.10.2026",
      Месяц: "01.09.2026 — 30.09.2026",
      Квартал: "01.07.2026 — 30.09.2026"
    };
    const rangeLabel = document.getElementById("analytics-range-label");
    const text = labels[button.dataset.analyticsPeriod] || labels.Месяц;
    if (rangeLabel) rangeLabel.textContent = text;
    rangeButton.setAttribute("aria-label", `Период: ${button.dataset.analyticsPeriod}, ${text}`);
  });
});

// The board supports pointer drag-and-drop plus keyboard movement with Alt + arrows.
let draggedCard = null;
function moveCardToColumn(card, destination, announce = true) {
  if (!card || !destination) return;
  const source = card.closest("[data-kanban-column]");
  if (source === destination) return;
  const sourceCount = source?.querySelector(".column-count");
  const destinationCount = destination.querySelector(".column-count");
  if (sourceCount && destinationCount) {
    sourceCount.textContent = String(Math.max(0, Number(sourceCount.textContent) - 1));
    destinationCount.textContent = String(Number(destinationCount.textContent) + 1);
  }
  destination.querySelector(".kanban-cards").appendChild(card);
  card.classList.remove("is-dragging");
  source?.classList.remove("is-drop-target");
  destination.classList.remove("is-drop-target");
  if (announce) showToast(`${card.dataset.cardName} перемещён на этап «${destination.dataset.kanbanColumn}»`);
}
document.querySelectorAll(".kanban-card").forEach((card) => {
  const owner = card.querySelector(".card-owner")?.textContent.trim();
  if (owner) card.dataset.owner = owner;
  if (card.dataset.cardName === "Анна Иванова" || card.textContent.includes("Сегодня")) card.dataset.urgent = "true";
  card.addEventListener("dragstart", (event) => {
    draggedCard = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.cardName || "Кандидат");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("is-dragging");
    document.querySelectorAll(".kanban-column.is-drop-target").forEach((column) => column.classList.remove("is-drop-target"));
    draggedCard = null;
  });
  card.addEventListener("keydown", (event) => {
    if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const columns = Array.from(document.querySelectorAll("[data-kanban-column]"));
    const current = card.closest("[data-kanban-column]");
    const index = columns.indexOf(current) + (event.key === "ArrowRight" ? 1 : -1);
    if (columns[index]) {
      moveCardToColumn(card, columns[index]);
      card.focus();
    }
  });
});
document.querySelectorAll("[data-kanban-column]").forEach((column) => {
  column.addEventListener("dragover", (event) => { event.preventDefault(); if (draggedCard && draggedCard.closest("[data-kanban-column]") !== column) column.classList.add("is-drop-target"); });
  column.addEventListener("dragleave", (event) => { if (!column.contains(event.relatedTarget)) column.classList.remove("is-drop-target"); });
  column.addEventListener("drop", (event) => { event.preventDefault(); if (draggedCard) moveCardToColumn(draggedCard, column); });
});
document.querySelectorAll("[data-board-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-board-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
    const filter = button.dataset.boardFilter;
    document.querySelectorAll(".kanban-card").forEach((card) => {
      const visible = filter === "all" || (filter === "mine" && (card.dataset.owner === "АМ" || card.dataset.cardName === "Анна Иванова" || card.dataset.cardName === "Елена Морозова")) || (filter === "urgent" && card.dataset.urgent === "true");
      card.hidden = !visible;
    });
  });
});

// Document-list wizard, with a live preview of the title and stage.
let wizardStep = 1;
const wizardPanels = document.querySelectorAll("[data-step-panel]");
function setWizardStep(step) {
  wizardStep = step;
  wizardPanels.forEach((panel) => { panel.hidden = Number(panel.dataset.stepPanel) !== step; });
  document.querySelectorAll("[data-step-indicator]").forEach((indicator) => {
    const value = Number(indicator.dataset.stepIndicator);
    indicator.classList.toggle("is-current", value === step);
    indicator.classList.toggle("is-complete", value < step);
  });
  document.getElementById("step-counter").innerHTML = `${String(step).padStart(2, "0")} <i>/</i> 02`;
  document.getElementById("wizard-error").hidden = true;
}
document.getElementById("wizard-next").addEventListener("click", () => {
  const name = document.getElementById("list-name");
  if (!name.value.trim()) {
    document.getElementById("wizard-error").hidden = false;
    name.focus();
    name.setAttribute("aria-invalid", "true");
    return;
  }
  name.removeAttribute("aria-invalid");
  setWizardStep(2);
});
document.getElementById("wizard-back").addEventListener("click", () => setWizardStep(1));
document.getElementById("list-name").addEventListener("input", (event) => {
  const value = event.target.value.trim();
  document.getElementById("preview-list-title").textContent = value || "Название списка";
  if (value) {
    document.getElementById("wizard-error").hidden = true;
    event.target.removeAttribute("aria-invalid");
  }
});
document.getElementById("list-stage").addEventListener("change", (event) => {
  document.getElementById("preview-list-stage").textContent = event.target.value || "Этап не выбран";
});
document.getElementById("document-list-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (wizardStep !== 2) return;
  const name = document.getElementById("list-name").value.trim() || "Новый список";
  wizardPanels.forEach((panel) => { panel.hidden = true; });
  document.querySelector(".wizard-stepper").hidden = true;
  document.getElementById("wizard-success").hidden = false;
  document.getElementById("success-list-name").textContent = `Список «${name}» создан и доступен команде.`;
  document.getElementById("wizard-card-title").textContent = "Готово к работе";
  showToast(`Список «${name}» создан`);
});

// Count-up and staggered entrance are intentionally short and respect reduced motion.
function animateCounters() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-counter]").forEach((node) => {
    const end = Number(node.dataset.counter);
    if (!Number.isFinite(end)) return;
    if (reduceMotion) { node.textContent = String(end); return; }
    const start = performance.now();
    const duration = 620;
    function tick(now) {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = String(Math.round(end * eased));
      if (progress < 1) requestAnimationFrame(tick);
      else node.textContent = String(end);
    }
    requestAnimationFrame(tick);
  });
}
window.setTimeout(() => {
  document.body.classList.remove("is-loading");
  document.body.classList.add("is-ready");
  animateCounters();
}, 720);

// Make dynamically generated icons in wizard/card/dialog content consistent after an interaction.
document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-icon-render-needed]");
  if (target) renderIcons(target);
});
