(() => {
  const root = document.documentElement;
  const body = document.body;
  const routeTitles = {
    inbox: 'Моя очередь',
    candidates: 'Кандидаты',
    pipeline: 'Воронка',
    analytics: 'Аналитика',
    documents: 'Списки документов',
    calendar: 'Календарь',
    schedule: 'График выхода',
    materials: 'Шаблоны и материалы',
    notifications: 'Уведомления',
    settings: 'Настройки',
  };
  const routes = new Set(Object.keys(routeTitles));
  const screens = [...document.querySelectorAll('[data-screen]')];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let wizardStep = 1;
  let commandOpen = false;
  let modalOpen = false;
  let commandActiveIndex = -1;
  let modalReturnFocus = null;
  let toastSequence = 0;
  let calendarWeek = 0;

  const safelyGetPreference = (key, fallback) => {
    try { return window.localStorage.getItem(key) || fallback; } catch { return fallback; }
  };
  const safelySetPreference = (key, value) => {
    try { window.localStorage.setItem(key, value); } catch { /* private mode: keep session-only state */ }
  };

  const initialTheme = safelyGetPreference('hrm-agent3-theme', 'light');
  const initialDensity = safelyGetPreference('hrm-agent3-density', 'comfortable');
  const initialFonts = safelyGetPreference('hrm-agent3-fonts', 'false');
  root.dataset.theme = initialTheme === 'dark' ? 'dark' : 'light';
  root.dataset.density = initialDensity === 'compact' ? 'compact' : 'comfortable';
  root.dataset.systemFonts = initialFonts === 'true' ? 'true' : 'false';
  syncDisplayControls();

  function syncDisplayControls() {
    const dark = root.dataset.theme === 'dark';
    const compact = root.dataset.density === 'compact';
    const systemFonts = root.dataset.systemFonts === 'true';
    document.querySelectorAll('[data-theme-label]').forEach((label) => { label.textContent = dark ? 'Тёмная' : 'Светлая'; });
    document.querySelectorAll('[data-density-label]').forEach((label) => { label.textContent = compact ? 'Компактная' : 'Комфортная'; });
    document.querySelectorAll('[data-font-label]').forEach((label) => { label.textContent = systemFonts ? 'Системный' : 'Авторский'; });
    document.querySelectorAll('[data-action="theme"]').forEach((button) => {
      button.setAttribute('aria-pressed', String(dark));
      const use = button.querySelector('.theme-icon use');
      if (use) use.setAttribute('href', dark ? '#i-moon' : '#i-sun');
      button.setAttribute('aria-label', `Тема: ${dark ? 'тёмная' : 'светлая'}. Переключить тему`);
    });
    document.querySelectorAll('[data-action="density"]').forEach((button) => {
      button.setAttribute('aria-pressed', String(compact));
      button.setAttribute('aria-label', `Плотность: ${compact ? 'компактная' : 'комфортная'}. Переключить плотность`);
    });
    document.querySelectorAll('[data-action="fonts"]').forEach((button) => {
      button.setAttribute('aria-pressed', String(systemFonts));
      button.setAttribute('aria-label', `Шрифт: ${systemFonts ? 'системный' : 'авторский'}. Переключить шрифт`);
    });
  }

  function navigate(route, options = {}) {
    if (!routes.has(route)) route = 'inbox';
    const nextHash = `#${route}`;
    if (window.location.hash === nextHash) {
      renderRoute(route);
      return;
    }
    if (options.replace) window.history.replaceState(null, '', nextHash);
    else window.location.hash = route;
    renderRoute(route);
  }

  function renderRoute(routeFromHash) {
    let route = routeFromHash || window.location.hash.replace(/^#/, '') || 'inbox';
    if (!routes.has(route)) {
      route = 'inbox';
      window.history.replaceState(null, '', '#inbox');
    }
    screens.forEach((screen) => {
      const active = screen.dataset.screen === route;
      screen.hidden = !active;
      screen.classList.toggle('is-active', active);
    });
    document.querySelectorAll('[data-route]').forEach((link) => {
      if (link.dataset.route === route) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    const title = routeTitles[route];
    const breadcrumb = document.getElementById('breadcrumb-label');
    if (breadcrumb) breadcrumb.textContent = title;
    document.title = `${title} — HR-Manager`;
    animateCounters(document.querySelector(`[data-screen="${route}"]`));
  }

  function animateCounters(container) {
    if (!container) return;
    container.querySelectorAll('[data-count]').forEach((element) => {
      if (element.dataset.animated === 'true') return;
      element.dataset.animated = 'true';
      const target = Number(element.dataset.count);
      if (!Number.isFinite(target)) return;
      if (reducedMotion) {
        setCounterText(element, target);
        return;
      }
      const suffix = element.dataset.suffix || '';
      const decimals = Number(element.dataset.decimals || 0);
      const start = performance.now();
      const duration = 560;
      const frame = (now) => {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const value = target * eased;
        element.textContent = `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: decimals, minimumFractionDigits: decimals }).format(progress === 1 ? target : value)}${suffix}`;
        if (progress < 1) requestAnimationFrame(frame);
      };
      element.textContent = `0${suffix}`;
      requestAnimationFrame(frame);
    });
  }

  function setCounterText(element, value) {
    const decimals = Number(element.dataset.decimals || 0);
    element.textContent = `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: decimals, minimumFractionDigits: decimals }).format(value)}${element.dataset.suffix || ''}`;
  }

  function showToast(title, message, type = 'success') {
    const region = document.getElementById('toast-region');
    const toast = document.createElement('div');
    const iconHref = type === 'info' ? '#i-spark' : '#i-check';
    toast.className = `toast${type === 'info' ? ' toast-info' : ''}`;
    toast.setAttribute('role', 'status');
    toast.dataset.toastId = String(++toastSequence);
    const iconWrap = document.createElement('span');
    iconWrap.className = 'toast-icon';
    iconWrap.innerHTML = `<svg class="icon" aria-hidden="true"><use href="${iconHref}"></use></svg>`;
    const copy = document.createElement('span');
    copy.className = 'toast-copy';
    const strong = document.createElement('strong');
    strong.textContent = title;
    const paragraph = document.createElement('p');
    paragraph.textContent = message;
    copy.append(strong, paragraph);
    const close = document.createElement('button');
    close.className = 'toast-close';
    close.type = 'button';
    close.setAttribute('aria-label', 'Закрыть уведомление');
    close.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg>';
    close.addEventListener('click', () => toast.remove());
    toast.append(iconWrap, copy, close);
    region.append(toast);
    window.setTimeout(() => {
      if (toast.isConnected) toast.remove();
    }, 4400);
  }

  function openCommandPalette() {
    const dialog = document.getElementById('command-dialog');
    const backdrop = document.getElementById('command-backdrop');
    if (commandOpen) return;
    commandOpen = true;
    dialog.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add('has-modal');
    const input = document.getElementById('command-input');
    filterCommandOptions('');
    commandActiveIndex = -1;
    window.setTimeout(() => input.focus(), 0);
  }

  function closeCommandPalette() {
    if (!commandOpen) return;
    commandOpen = false;
    document.getElementById('command-dialog').hidden = true;
    document.getElementById('command-backdrop').hidden = true;
    if (!modalOpen) document.body.classList.remove('has-modal');
    document.querySelector('[data-action="open-command"]')?.focus({ preventScroll: true });
  }

  function filterCommandOptions(query) {
    const normalized = query.trim().toLocaleLowerCase('ru');
    const options = [...document.querySelectorAll('.command-option')];
    let visibleCount = 0;
    options.forEach((option) => {
      const match = !normalized || option.textContent.toLocaleLowerCase('ru').includes(normalized);
      option.hidden = !match;
      option.classList.remove('is-active');
      if (match) visibleCount += 1;
    });
    const labels = document.querySelectorAll('.command-group-label');
    const sectionOptions = options.filter((option) => option.hasAttribute('data-command-route'));
    const candidateOptions = options.filter((option) => option.hasAttribute('data-command-candidate'));
    if (labels[0]) labels[0].hidden = !sectionOptions.some((option) => !option.hidden);
    if (labels[1]) labels[1].hidden = !candidateOptions.some((option) => !option.hidden);
    document.getElementById('command-empty').hidden = visibleCount > 0;
    commandActiveIndex = -1;
  }

  function visibleCommandOptions() {
    return [...document.querySelectorAll('.command-option')].filter((option) => !option.hidden);
  }

  function setCommandActive(index) {
    const options = visibleCommandOptions();
    if (!options.length) return;
    commandActiveIndex = (index + options.length) % options.length;
    options.forEach((option, optionIndex) => {
      option.classList.toggle('is-active', optionIndex === commandActiveIndex);
    });
    options[commandActiveIndex].scrollIntoView({ block: 'nearest' });
  }

  function openFormModal() {
    const modal = document.getElementById('candidate-modal');
    modalReturnFocus = document.activeElement;
    modal.hidden = false;
    document.getElementById('form-backdrop').hidden = false;
    document.body.classList.add('has-modal');
    modalOpen = true;
    window.setTimeout(() => modal.querySelector('input')?.focus(), 0);
  }

  function closeFormModal() {
    if (!modalOpen) return;
    document.getElementById('candidate-modal').hidden = true;
    document.getElementById('form-backdrop').hidden = true;
    modalOpen = false;
    if (!commandOpen) document.body.classList.remove('has-modal');
    if (modalReturnFocus?.isConnected) modalReturnFocus.focus({ preventScroll: true });
    modalReturnFocus = null;
  }

  function appendCandidateRow(name, role, source) {
    const tableBody = document.querySelector('#candidate-table tbody');
    const tr = document.createElement('tr');
    const tokens = name.trim().split(/\s+/).slice(0, 2);
    const initials = tokens.map((part) => part[0] || '').join('').toLocaleUpperCase('ru');
    const normalized = `${name} ${role}`.toLocaleLowerCase('ru');
    tr.dataset.candidateRow = '';
    tr.dataset.search = normalized;

    const selectCell = document.createElement('td');
    selectCell.className = 'check-column';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.setAttribute('aria-label', `Выбрать ${name}`);
    selectCell.append(checkbox);

    const personCell = document.createElement('td');
    const person = document.createElement('div');
    person.className = 'candidate-person';
    const avatar = document.createElement('span');
    avatar.className = 'avatar avatar-green';
    avatar.textContent = initials;
    const info = document.createElement('span');
    const candidateButton = document.createElement('button');
    candidateButton.className = 'candidate-name';
    candidateButton.type = 'button';
    candidateButton.dataset.candidateSelect = '';
    candidateButton.dataset.name = name;
    candidateButton.dataset.role = role || 'Должность не указана';
    candidateButton.dataset.stage = 'Отклик';
    candidateButton.dataset.source = source;
    candidateButton.dataset.initials = initials;
    candidateButton.dataset.location = 'Не указан';
    candidateButton.textContent = name;
    const detail = document.createElement('small');
    detail.textContent = `${role || 'Должность не указана'} · Не указан`;
    info.append(candidateButton, detail);
    person.append(avatar, info);
    personCell.append(person);

    const stageCell = document.createElement('td');
    stageCell.innerHTML = '<span class="status-chip status-new"><i></i>Отклик</span>';
    const sourceCell = document.createElement('td');
    sourceCell.textContent = source;
    const dateCell = document.createElement('td');
    dateCell.className = 'table-date';
    dateCell.textContent = '01.10.2026';
    const actionCell = document.createElement('td');
    const actionButton = document.createElement('button');
    actionButton.className = 'icon-button table-more';
    actionButton.type = 'button';
    actionButton.setAttribute('aria-label', `Действия для ${name}`);
    actionButton.dataset.action = 'row-menu';
    actionButton.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-dots"></use></svg>';
    actionCell.append(actionButton);
    tr.append(selectCell, personCell, stageCell, sourceCell, dateCell, actionCell);
    tableBody.prepend(tr);
    applyCandidateFilters();
  }

  function transliterateName(value) {
    const letters = { а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya' };
    return value.toLocaleLowerCase('ru').split(/\s+/).reverse().map((part) => [...part].map((letter) => letters[letter] ?? letter).join('')).join('.').replace(/[^a-z0-9.-]/g, '');
  }

  function openCandidate(button) {
    const drawer = document.getElementById('candidate-drawer');
    const layout = document.getElementById('candidate-layout');
    const name = button.dataset.name || button.textContent.trim();
    const role = button.dataset.role || 'Должность не указана';
    const stage = button.dataset.stage || 'Отклик';
    const stageClass = ({
      'Отклик': 'status-new',
      'Звонок': 'status-call',
      'Собеседование': 'status-interview',
      'Оффер': 'status-offer',
      'Выход': 'status-start',
    })[stage] || 'status-neutral';
    document.getElementById('drawer-name').textContent = name;
    document.getElementById('drawer-role').textContent = role;
    document.getElementById('drawer-stage').textContent = stage;
    document.getElementById('drawer-location').textContent = button.dataset.location || 'Не указан';
    document.getElementById('drawer-avatar').textContent = button.dataset.initials || name.split(/\s+/).slice(0, 2).map((part) => part[0] || '').join('').toLocaleUpperCase('ru');
    document.getElementById('drawer-status').className = `status-chip ${stageClass}`;
    const email = `${transliterateName(name) || 'candidate'}@example.com`;
    document.getElementById('drawer-email').href = `mailto:${email}`;
    document.getElementById('drawer-email-label').textContent = email;
    document.getElementById('drawer-phone').textContent = name === 'Баев Константин' ? '+7 (903) 555-28-14' : 'Телефон указан в анкете';
    drawer.hidden = false;
    layout.classList.remove('drawer-closed');
    selectCandidateTab('messages');
  }

  function selectCandidateTab(tabName, focus = false) {
    document.querySelectorAll('[data-candidate-tab]').forEach((button) => {
      const selected = button.dataset.candidateTab === tabName;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    });
    document.querySelectorAll('.tab-panel[id^="tab-panel-"]').forEach((panel) => {
      panel.hidden = panel.id !== `tab-panel-${tabName}`;
    });
  }

  function applyCandidateFilters() {
    const query = (document.getElementById('candidate-search')?.value || '').trim().toLocaleLowerCase('ru');
    const stage = document.getElementById('candidate-stage-filter')?.value || 'all';
    const rows = [...document.querySelectorAll('[data-candidate-row]')];
    let visible = 0;
    rows.forEach((row) => {
      const textMatch = !query || (row.dataset.search || row.textContent).toLocaleLowerCase('ru').includes(query);
      const rowStage = row.querySelector('.status-chip')?.textContent.trim() || '';
      const stageMatch = stage === 'all' || rowStage === stage;
      row.hidden = !(textMatch && stageMatch);
      if (!row.hidden) visible += 1;
    });
    const visibleCount = document.getElementById('candidate-visible-count');
    const footerCount = document.getElementById('candidate-footer-count');
    if (visibleCount) visibleCount.textContent = String(visible);
    if (footerCount) footerCount.textContent = String(visible);
  }

  function setWizardStep(step) {
    wizardStep = Math.max(1, Math.min(3, step));
    document.querySelectorAll('[data-wizard-panel]').forEach((panel) => {
      panel.hidden = Number(panel.dataset.wizardPanel) !== wizardStep;
    });
    document.querySelectorAll('[data-step-indicator]').forEach((indicator) => {
      const current = Number(indicator.dataset.stepIndicator);
      indicator.classList.toggle('is-current', current === wizardStep);
      indicator.classList.toggle('is-complete', current < wizardStep);
    });
    const progress = document.querySelector('.wizard-progress');
    if (progress) progress.setAttribute('aria-label', `Шаг ${wizardStep} из 3`);
    const back = document.querySelector('[data-action="wizard-back"]');
    if (back) back.hidden = wizardStep === 1;
    const nextLabel = document.querySelector('[data-wizard-next-label]');
    if (nextLabel) nextLabel.textContent = wizardStep === 3 ? 'Сохранить список' : 'Далее';
  }

  function updateDocumentPreview() {
    const name = document.querySelector('[name="list-name"]')?.value.trim() || 'Новый список документов';
    const preview = document.getElementById('preview-list-name');
    const review = document.getElementById('review-list-name');
    if (preview) preview.textContent = name;
    if (review) review.textContent = name;
    const checked = document.querySelectorAll('[data-wizard-panel="2"] input[type="checkbox"]:checked').length;
    const count = document.getElementById('review-document-count');
    if (count) count.textContent = `${checked} ${checked === 1 ? 'документ' : checked > 1 && checked < 5 ? 'документа' : 'документов'}`;
  }

  function updateKanbanCounts() {
    document.querySelectorAll('[data-stage-column]').forEach((column) => {
      const count = column.querySelector('.column-count');
      if (count) count.textContent = String(column.querySelectorAll('[data-kanban-card]').length);
    });
  }

  document.addEventListener('click', (event) => {
    const routeLink = event.target.closest('[data-route-link]');
    if (routeLink) {
      navigate(routeLink.dataset.routeLink);
      return;
    }
    const candidateSelect = event.target.closest('[data-candidate-select]');
    if (candidateSelect) {
      openCandidate(candidateSelect);
      return;
    }
    const tabButton = event.target.closest('[data-candidate-tab]');
    if (tabButton) {
      selectCandidateTab(tabButton.dataset.candidateTab);
      return;
    }
    const periodButton = event.target.closest('[data-period]');
    if (periodButton) {
      document.querySelectorAll('[data-period]').forEach((button) => {
        const active = button === periodButton;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
      });
      const period = periodButton.dataset.period;
      const range = ({
        '7 дней': '25.09.2026 — 01.10.2026',
        '30 дней': '01.09.2026 — 01.10.2026',
        'Квартал': '01.07.2026 — 01.10.2026',
        'Год': '01.01.2026 — 01.10.2026',
      })[period] || period;
      document.getElementById('analytics-period-label').textContent = range;
      const chartPeriod = document.querySelector('.chart-period-tag');
      if (chartPeriod) chartPeriod.textContent = period.toLocaleUpperCase('ru');
      showToast('Период обновлён', `Показаны данные: ${period.toLocaleLowerCase('ru')}.`, 'info');
      return;
    }
    const commandOption = event.target.closest('[data-command-route]');
    if (commandOption) {
      const route = commandOption.dataset.commandRoute;
      closeCommandPalette();
      navigate(route);
      return;
    }
    const candidateCommand = event.target.closest('[data-command-candidate]');
    if (candidateCommand) {
      const candidateName = candidateCommand.dataset.commandCandidate;
      closeCommandPalette();
      navigate('candidates');
      window.setTimeout(() => {
        const button = [...document.querySelectorAll('[data-candidate-select]')].find((item) => item.dataset.name === candidateName);
        if (button) {
          openCandidate(button);
          button.focus({ preventScroll: true });
        }
      }, 60);
      return;
    }
    const actionButton = event.target.closest('[data-action]');
    if (!actionButton) {
      if (event.target.id === 'command-backdrop') closeCommandPalette();
      if (event.target.id === 'form-backdrop') closeFormModal();
      if (event.target.id === 'command-backdrop') return;
      return;
    }
    const action = actionButton.dataset.action;
    switch (action) {
      case 'open-command': openCommandPalette(); break;
      case 'theme': {
        root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
        safelySetPreference('hrm-agent3-theme', root.dataset.theme);
        syncDisplayControls();
        showToast('Тема изменена', `Включена ${root.dataset.theme === 'dark' ? 'тёмная' : 'светлая'} тема.`, 'info');
        break;
      }
      case 'density': {
        root.dataset.density = root.dataset.density === 'compact' ? 'comfortable' : 'compact';
        safelySetPreference('hrm-agent3-density', root.dataset.density);
        syncDisplayControls();
        showToast('Плотность изменена', `Включён ${root.dataset.density === 'compact' ? 'компактный' : 'комфортный'} режим таблиц.`, 'info');
        break;
      }
      case 'fonts': {
        root.dataset.systemFonts = root.dataset.systemFonts === 'true' ? 'false' : 'true';
        safelySetPreference('hrm-agent3-fonts', root.dataset.systemFonts);
        syncDisplayControls();
        showToast('Шрифт изменён', root.dataset.systemFonts === 'true' ? 'Используется системный стек шрифтов.' : 'Используется авторская типографика, если шрифт доступен.', 'info');
        break;
      }
      case 'collapse-sidebar': {
        body.classList.toggle('sidebar-collapsed');
        const collapsed = body.classList.contains('sidebar-collapsed');
        safelySetPreference('hrm-agent3-sidebar-collapsed', String(collapsed));
        actionButton.setAttribute('aria-label', collapsed ? 'Развернуть меню' : 'Свернуть меню');
        actionButton.title = collapsed ? 'Развернуть меню' : 'Свернуть меню';
        break;
      }
      case 'refresh-dashboard': {
        const frame = document.getElementById('dashboard-chart-frame');
        frame.classList.add('is-loading');
        showToast('Обновляем сводку', 'Загружаем свежие показатели за октябрь.', 'info');
        window.setTimeout(() => {
          frame.classList.remove('is-loading');
          showToast('Данные обновлены', 'Сводка синхронизирована в 09:42.');
        }, 1150);
        break;
      }
      case 'add-candidate': openFormModal(); break;
      case 'close-form-modal': closeFormModal(); break;
      case 'close-drawer':
        document.getElementById('candidate-drawer').hidden = true;
        document.getElementById('candidate-layout').classList.add('drawer-closed');
        break;
      case 'complete-task': {
        const task = actionButton.closest('[data-task]');
        task?.classList.toggle('is-complete');
        const completed = task?.classList.contains('is-complete');
        actionButton.setAttribute('aria-label', completed ? 'Задача выполнена' : 'Отметить задачу выполненной');
        showToast(completed ? 'Задача выполнена' : 'Задача возвращена в очередь', completed ? 'Отметка сохранена в вашей очереди.' : 'Задача снова требует внимания.');
        break;
      }
      case 'navigate-analytics': navigate('analytics'); break;
      case 'candidate-filters': showToast('Фильтры кандидатов', 'В демо активны этап и дата последнего обновления.', 'info'); break;
      case 'export-candidates': showToast('Экспорт подготовлен', 'В рабочей версии файл будет сформирован в формате XLSX.', 'info'); break;
      case 'export-analytics': showToast('Отчёт подготовлен', 'В рабочей версии аналитика будет выгружена в XLSX.', 'info'); break;
      case 'row-menu': showToast('Действия профиля', 'Здесь будут доступны быстрые действия с записью.', 'info'); break;
      case 'toast-messages': showToast('Сообщение готово', 'Переписка доступна в карточке кандидата.'); break;
      case 'toast-upload': showToast('Загрузка файла', 'Выберите файл в рабочей версии HR-Manager.', 'info'); break;
      case 'toast-file': showToast('Предпросмотр документа', 'Открытие файла показано как демонстрационное действие.', 'info'); break;
      case 'transfer-candidate': showToast('Передача кандидата', 'Выберите коллегу в рабочей версии, чтобы передать профиль.', 'info'); break;
      case 'change-stage': {
        const current = document.getElementById('drawer-stage').textContent;
        const stages = ['Отклик', 'Звонок', 'Собеседование', 'Оффер', 'Выход'];
        const next = stages[(stages.indexOf(current) + 1) % stages.length];
        const selectedName = document.getElementById('drawer-name').textContent;
        document.getElementById('drawer-stage').textContent = next;
        const chip = document.getElementById('drawer-status');
        chip.className = `status-chip ${{ 'Отклик': 'status-new', 'Звонок': 'status-call', 'Собеседование': 'status-interview', 'Оффер': 'status-offer', 'Выход': 'status-start' }[next]}`;
        showToast('Этап обновлён', `${selectedName} перемещён на этап «${next}».`);
        break;
      }
      case 'pipeline-filter': showToast('Фильтры воронки', 'Показаны все активные вакансии и рекрутеры.', 'info'); break;
      case 'pipeline-reset': {
        document.querySelectorAll('[data-kanban-card]').forEach((card) => {
          const originalStage = card.dataset.initialStage;
          if (originalStage) document.querySelector(`[data-stage-column="${CSS.escape(originalStage)}"] [data-dropzone]`)?.append(card);
        });
        updateKanbanCounts();
        showToast('Воронка восстановлена', 'Карточки возвращены на исходные этапы.');
        break;
      }
      case 'add-document-row': showToast('Новый документ', 'Пользовательский документ добавлен в демо-список.', 'info'); break;
      case 'wizard-back': setWizardStep(wizardStep - 1); break;
      case 'wizard-next': {
        if (wizardStep === 1) {
          const nameField = document.querySelector('[name="list-name"]');
          if (!nameField.reportValidity()) return;
        }
        if (wizardStep < 3) {
          setWizardStep(wizardStep + 1);
          updateDocumentPreview();
        } else {
          const listName = document.getElementById('review-list-name').textContent;
          showToast('Список сохранён', `«${listName}» добавлен в библиотеку документов.`);
          setWizardStep(1);
        }
        break;
      }
      case 'toast-source': showToast('Источники кандидатов', 'Справочник источников доступен в настройках.', 'info'); break;
      case 'calendar-today': calendarWeek = 0; updateCalendarWeek(); showToast('Календарь', 'Показана текущая неделя.'); break;
      case 'calendar-prev': calendarWeek -= 1; updateCalendarWeek(); break;
      case 'calendar-next': calendarWeek += 1; updateCalendarWeek(); break;
      case 'add-event': showToast('Новое событие', 'Форма встречи доступна в рабочем календаре.', 'info'); break;
      case 'schedule-export': showToast('График подготовлен', 'Список выходов готов к выгрузке в Excel.', 'info'); break;
      case 'schedule-import': showToast('Импорт Excel', 'Выберите файл графика в рабочей версии приложения.', 'info'); break;
      case 'materials-filter': showToast('Категории материалов', 'Используйте быстрые фильтры под строкой поиска.', 'info'); break;
      case 'materials-add': showToast('Новый материал', 'Загрузка материала доступна в библиотеке команды.', 'info'); break;
      case 'open-material': showToast('Материал открыт', 'Предпросмотр документа показан как демо-действие.', 'info'); break;
      case 'notification-done': {
        const item = actionButton.closest('[data-notification]');
        item?.classList.toggle('is-complete');
        actionButton.textContent = item?.classList.contains('is-complete') ? 'Готово' : 'Выполнено';
        showToast('Напоминание обновлено', 'Статус уведомления сохранён.');
        break;
      }
      case 'mark-all-read':
        document.querySelectorAll('[data-notification]').forEach((item) => item.classList.add('is-complete'));
        document.querySelectorAll('[data-action="notification-done"]').forEach((button) => { button.textContent = 'Готово'; });
        showToast('Всё прочитано', 'Новые уведомления отмечены как просмотренные.');
        break;
      case 'show-notifications': navigate('notifications'); break;
      case 'user-menu': showToast('Профиль Марии', 'Управление профилем доступно в настройках команды.', 'info'); break;
      case 'settings-users': showToast('Пользователи команды', 'В рабочей версии здесь настраиваются роли и доступ.', 'info'); break;
      case 'next-page': showToast('Страница 2', 'В этой демо-таблице показаны первые 12 профилей.', 'info'); break;
      default: break;
    }
  });

  document.addEventListener('input', (event) => {
    if (event.target.id === 'candidate-search' || event.target.id === 'candidate-stage-filter') applyCandidateFilters();
    if (event.target.id === 'command-input') filterCommandOptions(event.target.value);
    if (event.target.name === 'list-name' || (event.target.closest('[data-wizard-panel="2"]') && event.target.type === 'checkbox')) updateDocumentPreview();
    if (event.target.closest('.materials-toolbar .search-field')) {
      const query = event.target.value.trim().toLocaleLowerCase('ru');
      document.querySelectorAll('.material-card').forEach((card) => { card.hidden = !card.textContent.toLocaleLowerCase('ru').includes(query); });
    }
  });

  document.addEventListener('change', (event) => {
    if (event.target.id === 'candidate-stage-filter') applyCandidateFilters();
    if (event.target.matches('.analytics-filter select')) {
      showToast('Фильтр применён', `Показаны данные: ${event.target.value.toLocaleLowerCase('ru')}.`, 'info');
    }
    if (event.target.matches('.timezone-filter select')) {
      showToast('Часовой пояс изменён', `Время показано: ${event.target.value}.`, 'info');
    }
  });

  document.addEventListener('submit', (event) => {
    if (event.target.id === 'candidate-form') {
      event.preventDefault();
      if (!event.target.reportValidity()) return;
      const data = new FormData(event.target);
      const name = String(data.get('candidate-name') || '').trim();
      const role = String(data.get('candidate-role') || '').trim();
      const source = String(data.get('candidate-source') || 'Сайт компании');
      appendCandidateRow(name, role, source);
      event.target.reset();
      closeFormModal();
      navigate('candidates');
      showToast('Кандидат добавлен', `${name} появился в списке на этапе «Отклик».`);
    }
  });

  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (commandOpen) closeCommandPalette();
      else openCommandPalette();
      return;
    }
    if (event.key === 'Escape') {
      if (commandOpen) closeCommandPalette();
      else if (modalOpen) closeFormModal();
      return;
    }
    if (commandOpen) {
      const options = visibleCommandOptions();
      if (event.key === 'ArrowDown') { event.preventDefault(); setCommandActive(commandActiveIndex + 1); }
      if (event.key === 'ArrowUp') { event.preventDefault(); setCommandActive(commandActiveIndex - 1); }
      if (event.key === 'Enter' && options.length) {
        event.preventDefault();
        const selected = options[commandActiveIndex >= 0 ? commandActiveIndex : 0];
        selected?.click();
      }
      if (event.key === 'Tab') {
        const focusables = [...document.querySelectorAll('#command-dialog input:not([disabled]), #command-dialog button:not([disabled])')].filter((item) => !item.hidden);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    if (event.target.matches('[data-candidate-tab]') && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[data-candidate-tab]')];
      const index = tabs.indexOf(event.target);
      const step = event.key === 'ArrowRight' ? 1 : -1;
      const next = tabs[(index + step + tabs.length) % tabs.length];
      selectCandidateTab(next.dataset.candidateTab, true);
    }
    if (event.target.matches('[data-kanban-card]') && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
      event.preventDefault();
      const card = event.target;
      const columns = [...document.querySelectorAll('[data-stage-column]')];
      const currentColumn = card.closest('[data-stage-column]');
      const currentIndex = columns.indexOf(currentColumn);
      const nextIndex = Math.max(0, Math.min(columns.length - 1, currentIndex + (event.key === 'ArrowRight' ? 1 : -1)));
      if (nextIndex !== currentIndex) {
        const name = card.dataset.name;
        const destination = columns[nextIndex].querySelector('[data-dropzone]');
        destination.append(card);
        updateKanbanCounts();
        showToast('Кандидат перемещён', `${name} · этап «${columns[nextIndex].dataset.stageColumn}».`);
        card.focus({ preventScroll: true });
      }
    }
    if (modalOpen && event.key === 'Tab') {
      const modal = document.getElementById('candidate-modal');
      const focusables = [...modal.querySelectorAll('input:not([disabled]), select:not([disabled]), button:not([disabled])')].filter((item) => !item.hidden);
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  document.querySelectorAll('[data-period]').forEach((button) => button.setAttribute('aria-pressed', String(button.classList.contains('is-active'))));
  document.querySelectorAll('[data-kanban-card]').forEach((card) => {
    card.dataset.initialStage = card.closest('[data-stage-column]')?.dataset.stageColumn || '';
    card.addEventListener('dragstart', (event) => {
      event.dataTransfer?.setData('text/plain', card.dataset.name || 'candidate');
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
      card.classList.add('is-dragging');
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('is-dragging');
      document.querySelectorAll('.kanban-list.is-drop-target').forEach((zone) => zone.classList.remove('is-drop-target'));
    });
  });
  document.querySelectorAll('[data-dropzone]').forEach((zone) => {
    zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('is-drop-target'); });
    zone.addEventListener('dragleave', (event) => {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-drop-target');
    });
    zone.addEventListener('drop', (event) => {
      event.preventDefault();
      zone.classList.remove('is-drop-target');
      const name = event.dataTransfer?.getData('text/plain');
      const card = [...document.querySelectorAll('[data-kanban-card]')].find((candidate) => candidate.dataset.name === name);
      if (!card || card.parentElement === zone) return;
      const destinationColumn = zone.closest('[data-stage-column]');
      zone.append(card);
      updateKanbanCounts();
      showToast('Кандидат перемещён', `${name} · этап «${destinationColumn.dataset.stageColumn}».`);
    });
  });

  document.querySelectorAll('.category-pill').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.category-pill').forEach((pill) => {
        const active = pill === button;
        pill.classList.toggle('is-active', active);
        pill.setAttribute('aria-pressed', String(active));
      });
      const category = button.textContent.trim().split(/\s+/)[0].toLocaleLowerCase('ru');
      const materialCards = [...document.querySelectorAll('.material-card')];
      materialCards.forEach((card, index) => {
        const categories = ['подбор', 'подбор', 'документы', 'адаптация'];
        card.hidden = category !== 'все' && categories[index] !== category;
      });
    });
  });

  document.querySelectorAll('.view-button').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.view-button').forEach((view) => {
        const active = view === button;
        view.classList.toggle('is-selected', active);
        view.setAttribute('aria-pressed', String(active));
      });
      showToast(button.getAttribute('aria-label'), 'Представление списка переключено.', 'info');
    });
  });

  document.querySelectorAll('.calendar-event').forEach((item) => {
    item.addEventListener('click', () => showToast('Событие календаря', `${item.querySelector('strong')?.textContent || 'Встреча'} · доступно в демо.`, 'info'));
    item.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); item.click(); } });
  });
  document.querySelectorAll('.calendar-toolbar .segmented-control button').forEach((button) => {
    button.addEventListener('click', () => {
      button.parentElement.querySelectorAll('button').forEach((item) => {
        const active = item === button;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      showToast('Вид календаря', `Выбран вид «${button.textContent.trim().toLocaleLowerCase('ru')}».`, 'info');
    });
  });

  document.querySelectorAll('[data-settings-tab]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      document.querySelectorAll('[data-settings-tab]').forEach((item) => item.classList.toggle('is-active', item === link));
      showToast(link.textContent.trim(), 'Раздел настроек доступен в рабочей версии.', 'info');
    });
  });

  function updateCalendarWeek() {
    const label = document.querySelector('.calendar-week-nav strong');
    const labels = ['28 сентября — 4 октября 2026', '5 — 11 октября 2026', '12 — 18 октября 2026', '19 — 25 октября 2026', '26 октября — 1 ноября 2026'];
    if (!label) return;
    const index = Math.max(0, Math.min(labels.length - 1, calendarWeek + 0));
    label.textContent = labels[index];
    if (calendarWeek < 0) calendarWeek = 0;
    if (calendarWeek >= labels.length) calendarWeek = labels.length - 1;
  }

  document.getElementById('command-input').addEventListener('input', (event) => filterCommandOptions(event.target.value));
  document.getElementById('form-backdrop').addEventListener('click', closeFormModal);
  document.getElementById('command-backdrop').addEventListener('click', closeCommandPalette);
  document.getElementById('candidate-form').addEventListener('reset', () => window.setTimeout(() => document.getElementById('candidate-form').querySelector('input')?.focus(), 0));
  document.querySelector('[name="list-name"]')?.addEventListener('input', updateDocumentPreview);
  document.querySelectorAll('[data-wizard-panel="2"] input[type="checkbox"]').forEach((checkbox) => checkbox.addEventListener('change', updateDocumentPreview));

  if (safelyGetPreference('hrm-agent3-sidebar-collapsed', 'false') === 'true') body.classList.add('sidebar-collapsed');
  const sidebarToggle = document.querySelector('[data-action="collapse-sidebar"]');
  if (sidebarToggle && body.classList.contains('sidebar-collapsed')) {
    sidebarToggle.setAttribute('aria-label', 'Развернуть меню');
    sidebarToggle.title = 'Развернуть меню';
  }
  if (!window.location.hash || !routes.has(window.location.hash.slice(1))) {
    window.history.replaceState(null, '', '#inbox');
  }
  renderRoute();
  window.addEventListener('hashchange', () => renderRoute());
})();
