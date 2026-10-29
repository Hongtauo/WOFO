(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const svgNamespace = 'http://www.w3.org/2000/svg';
  const dialogs = [$('account-dialog'), $('settings-dialog'), $('delete-dialog')];
  let state = null;
  let ready = false;
  let mutationPending = false;
  let polling = false;
  let pollTimer = null;
  let revision = 0;
  let accountFingerprint = '';
  let lastEventId = null;
  let receivedFirstState = false;
  let editingAccountId = null;
  let deletingAccountId = null;
  let dialogSubmitting = false;
  let uninstallRequestPending = false;
  let accountFocusToRestore = null;
  let currentPage = 'overview';
  let logFingerprint = '';
  let followLogTail = true;

  function icon(name) {
    const element = document.createElementNS(svgNamespace, 'svg');
    element.classList.add('icon');
    element.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(svgNamespace, 'use');
    use.setAttribute('href', `#i-${name}`);
    element.append(use);
    return element;
  }

  function toast(message, level = 'success') {
    if (!message) return;
    const item = document.createElement('div');
    item.className = 'toast';
    if (['error', 'warning'].includes(level)) item.classList.add(`is-${level}`);
    const text = document.createElement('span');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭提示');
    close.append(icon('close'));
    close.addEventListener('click', () => item.remove());
    item.append(text, close);
    $('toast-region').append(item);
    while ($('toast-region').childElementCount > 3) $('toast-region').firstElementChild.remove();
    window.setTimeout(() => item.remove(), level === 'error' ? 9000 : 5000);
  }

  function bridgeError(message) {
    $('bridge-banner').hidden = false;
    $('bridge-banner').classList.add('is-error');
    $('bridge-message').textContent = message;
    $('retry-button').hidden = false;
  }

  function selectedAccount() {
    return state?.accounts.find((account) => account.id === state.selected_account_id) || null;
  }

  function canInteract() {
    return ready && !!state && !mutationPending && !state.busy;
  }

  function accountLimit() {
    return Number.isInteger(state?.account_limit) && state.account_limit > 0 ? state.account_limit : 5;
  }

  function navigate() {
    currentPage = window.location.hash === '#logs' ? 'logs' : 'overview';
    const showingLogs = currentPage === 'logs';
    $('overview-page').hidden = showingLogs;
    $('logs-page').hidden = !showingLogs;
    [['overview-nav', !showingLogs], ['logs-nav', showingLogs]].forEach(([id, active]) => {
      $(id).classList.toggle('active', active);
      if (active) $(id).setAttribute('aria-current', 'page');
      else $(id).removeAttribute('aria-current');
    });
    $('page-eyebrow').textContent = showingLogs ? 'EVERY CONNECTION, RECORDED' : 'YOUR CAMPUS, CONNECTED';
    $('page-title').textContent = showingLogs ? '运行日志' : '连接概览';
    $('page-description').textContent = showingLogs ? '连接与守护的每一步，都有迹可循。' : '管理校园网账号，保持每一次连接。';
    if (showingLogs && followLogTail) {
      window.requestAnimationFrame(() => { $('logs-viewport').scrollTop = $('logs-viewport').scrollHeight; });
    }
  }

  function renderLogs() {
    const logs = Array.isArray(state.logs) ? state.logs : [];
    $('log-count').textContent = `${logs.length} 条`;
    $('log-capacity').textContent = `仅记录本次运行 · 保留最近 ${state.log_limit || 500} 条`;
    $('clear-logs-button').disabled = !ready || mutationPending || !logs.length;
    const fingerprint = JSON.stringify(logs);
    if (logFingerprint === fingerprint) return;
    logFingerprint = fingerprint;
    const viewport = $('logs-viewport');
    const scrollTop = viewport.scrollTop;
    const viewportTop = viewport.getBoundingClientRect().top;
    const anchor = currentPage === 'logs' && !followLogTail
      ? Array.from($('log-list').children).find((entry) => entry.getBoundingClientRect().bottom > viewportTop)
      : null;
    const anchorId = anchor?.dataset.logId;
    const anchorOffset = anchor ? anchor.getBoundingClientRect().top - viewportTop : 0;
    const fragment = document.createDocumentFragment();
    const levels = { info: '信息', success: '成功', warning: '提醒', error: '错误' };
    logs.forEach((entry) => {
      const item = document.createElement('li');
      const level = Object.hasOwn(levels, entry.level) ? entry.level : 'info';
      item.className = `log-entry log-${level}`;
      item.dataset.logId = String(entry.id);
      const time = document.createElement('time');
      const date = new Date(entry.time);
      if (!Number.isNaN(date.getTime())) {
        time.dateTime = date.toISOString();
        time.textContent = date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        time.title = date.toLocaleString('zh-CN', { hour12: false });
      } else {
        time.textContent = '—';
      }
      const badge = document.createElement('span');
      badge.className = 'log-level';
      badge.textContent = levels[level];
      const message = document.createElement('span');
      message.className = 'log-message';
      message.textContent = String(entry.message || '');
      item.append(time, badge, message);
      fragment.append(item);
    });
    $('log-list').replaceChildren(fragment);
    $('logs-empty').hidden = logs.length > 0;
    if (currentPage === 'logs') {
      if (followLogTail) viewport.scrollTop = viewport.scrollHeight;
      else {
        const replacement = Array.from($('log-list').children).find((entry) => entry.dataset.logId === anchorId);
        if (replacement) viewport.scrollTop += replacement.getBoundingClientRect().top - viewportTop - anchorOffset;
        else viewport.scrollTop = scrollTop;
      }
    }
    if (!logs.length) followLogTail = true;
  }

  function renderAccounts() {
    const fingerprint = JSON.stringify([state.accounts, state.selected_account_id, canInteract()]);
    if (accountFingerprint === fingerprint) return;
    accountFingerprint = fingerprint;
    const focused = document.activeElement;
    const focusedKey = focused?.dataset?.focusKey || accountFocusToRestore;
    if (focused?.dataset?.focusKey) accountFocusToRestore = focused.dataset.focusKey;
    const rows = document.createDocumentFragment();
    state.accounts.forEach((account) => {
      const row = document.createElement('tr');
      if (account.id === state.selected_account_id) row.classList.add('selected');
      const cell = document.createElement('td');
      const label = document.createElement('label');
      label.className = 'account-selector';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'selected-account';
      radio.value = String(account.id);
      radio.checked = account.id === state.selected_account_id;
      radio.disabled = !canInteract();
      radio.dataset.focusKey = `select-${account.id}`;
      radio.setAttribute('aria-label', `选择 ${account.user_id} 作为守护账号`);
      radio.addEventListener('change', () => mutate('select_account', [account.id], { quiet: true }));
      const name = document.createElement('span');
      name.className = 'account-name';
      name.textContent = account.user_id;
      name.title = account.user_id;
      if (account.decrypt_error) {
        const warning = document.createElement('small');
        warning.textContent = '密码不可用，请编辑更新';
        name.append(warning);
      }
      label.append(radio, name);
      cell.append(label);
      const serviceCell = document.createElement('td');
      const service = document.createElement('span');
      service.className = 'service-badge';
      service.textContent = account.service;
      serviceCell.append(service);
      const defaultCell = document.createElement('td');
      if (account.id === state.selected_account_id) {
        const badge = document.createElement('span');
        badge.className = 'default-tag';
        badge.append(icon('check'), document.createTextNode('已选择'));
        defaultCell.append(badge);
      } else {
        const inactive = document.createElement('span');
        inactive.className = 'inactive-guard';
        inactive.textContent = '—';
        defaultCell.append(inactive);
      }
      const actionsCell = document.createElement('td');
      const actions = document.createElement('div');
      actions.className = 'row-actions';
      [['edit', '编辑', openAccountDialog], ['trash', '删除', openDeleteDialog]].forEach(([symbol, title, action]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `icon-button ${symbol === 'trash' ? 'delete-button' : ''}`;
        button.title = `${title}账号`;
        button.setAttribute('aria-label', `${title}账号 ${account.user_id}`);
        button.dataset.focusKey = `${symbol}-${account.id}`;
        button.disabled = !canInteract();
        button.append(icon(symbol));
        button.addEventListener('click', () => action(account));
        actions.append(button);
      });
      actionsCell.append(actions);
      row.append(cell, serviceCell, defaultCell, actionsCell);
      rows.append(row);
    });
    $('account-list').replaceChildren(rows);
    if (focusedKey && canInteract() && !dialogs.some((dialog) => dialog.open)) {
      const replacement = Array.from($('account-list').querySelectorAll('[data-focus-key]')).find((element) => element.dataset.focusKey === focusedKey);
      replacement?.focus({ preventScroll: true });
      accountFocusToRestore = null;
    }
    $('account-table').hidden = state.accounts.length === 0;
    $('accounts-placeholder').hidden = state.accounts.length > 0;
    $('accounts-placeholder-title').textContent = '还没有校园网账号';
    $('accounts-placeholder-description').textContent = '添加账号后，即可一键连接校园网。';
    $('empty-add-button').hidden = false;
  }

  function checkedTime(value) {
    if (!value) return '等待首次检测';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '已完成网络检测';
    return `上次检测 ${date.toLocaleTimeString('zh-CN', { hour12: false })}`;
  }

  function render() {
    if (!state) return;
    const interactive = canInteract();
    const account = selectedAccount();
    const connected = state.network.connected;
    const networkBusy = !!state.busy;
    document.body.classList.toggle('is-busy', networkBusy);
    document.querySelector('.status-card').dataset.status = connected === true ? 'connected' : connected === false ? 'disconnected' : 'unknown';
    let label = connected === true ? '已连接' : connected === false ? '未连接' : '检测中';
    let heading = connected === true ? '校园网已连接' : connected === false ? '准备好，连接校园网' : '正在检测网络';
    let description = connected === true ? '网络畅通，可以开始你的校园生活了。' : connected === false ? '选择校园网账号，即可连接到校园网络。' : '正在确认网络连接状态，请稍候。';
    if (state.busy === 'login') {
      label = '连接中'; heading = '正在连接校园网'; description = '正在向校园网认证，请稍候。';
    } else if (state.busy === 'logout') {
      label = '断开中'; heading = '正在断开连接'; description = '正在退出校园网账号，请稍候。';
    }
    $('connection-label').textContent = label;
    $('art-status').textContent = label;
    $('status-heading').textContent = heading;
    $('status-description').textContent = description;
    $('connection-account').textContent = account ? `${account.user_id} · ${account.service}${account.decrypt_error ? ' · 请更新密码' : ''}` : '添加并选择账号后开始连接';
    $('last-checked').textContent = state.network.checking ? '正在检测网络…' : checkedTime(state.network.last_checked);
    $('login-label').textContent = state.busy === 'login' ? '正在连接…' : connected ? '重新连接' : '连接校园网';
    $('logout-label').textContent = state.busy === 'logout' ? '正在断开…' : '断开连接';
    $('login-button').disabled = !interactive || !account || account.decrypt_error;
    $('logout-button').disabled = !interactive;
    $('settings-nav').disabled = !interactive;
    const uninstallPending = !!state.uninstall_pending;
    if (!dialogSubmitting) {
      Array.from($('settings-dialog').querySelectorAll('button, input, select')).forEach((element) => { element.disabled = uninstallPending; });
    }
    $('uninstall-button').disabled = !interactive || dialogSubmitting || !state.can_uninstall || uninstallPending;
    $('uninstall-button').classList.toggle('is-loading', uninstallPending || uninstallRequestPending);
    $('uninstall-note').hidden = !!state.can_uninstall;
    const atCapacity = state.accounts.length >= accountLimit();
    ['add-account-button', 'empty-add-button'].forEach((id) => { $(id).disabled = !interactive || atCapacity; });
    $('add-account-button').title = atCapacity ? `已达到 ${accountLimit()} 个账号上限，可编辑或删除已有账号` : '添加校园网账号';
    // A slow network request must not prevent closing the application.
    $('quit-button').disabled = !ready || mutationPending;
    $('tray-button').disabled = !interactive || !state.tray_available;
    $('tray-button').title = state.tray_available ? '最小化到系统托盘' : '当前系统托盘不可用';
    $('daemon-switch').checked = !!state.daemon.enabled;
    $('daemon-switch').disabled = !interactive;
    const daemonLabel = state.daemon.active ? '守护中' : state.daemon.enabled ? '等待启动' : '未开启';
    $('daemon-badge').textContent = daemonLabel;
    $('daemon-badge').classList.toggle('is-active', !!state.daemon.active);
    const guardAccount = state.accounts.find((item) => item.id === state.daemon.account_id) || account;
    $('daemon-description').textContent = state.daemon.enabled && guardAccount
      ? `每 ${state.settings.check_interval} 秒检测网络，断线时使用 ${guardAccount.user_id} 自动重连。`
      : '自动检测网络，断线时使用选中的校园网账号重新连接。';
    $('account-count').textContent = `${state.accounts.length} / ${accountLimit()}`;
    $('account-capacity').textContent = atCapacity ? `已达 ${accountLimit()} 个上限，可编辑或删除` : `最多支持 ${accountLimit()} 个账号 · 仅保存在此设备`;
    $('account-capacity').classList.toggle('at-capacity', atCapacity);
    $('selection-hint').textContent = account ? `守护账号：${account.user_id}` : '请选择一个校园网账号进行守护';
    $('account-table').setAttribute('aria-busy', String(mutationPending));
    renderAccounts();
    renderLogs();
  }

  function acceptState(next) {
    if (!next || !Array.isArray(next.accounts) || !next.network || !next.daemon || !next.settings) {
      throw new Error('桌面服务返回了无效的状态数据');
    }
    state = next;
    ready = true;
    $('bridge-banner').hidden = true;
    const event = state.last_event;
    if (event && event.id !== lastEventId) {
      if (receivedFirstState) toast(event.message, event.level);
      lastEventId = event.id;
      $('last-event').textContent = event.message;
      $('last-event').className = ['error', 'success'].includes(event.level) ? `event-${event.level}` : '';
    }
    receivedFirstState = true;
    render();
  }

  async function poll() {
    if (polling) return;
    window.clearTimeout(pollTimer);
    if (!window.pywebview?.api) {
      bridgeError('桌面服务尚未就绪，请通过应用启动本页面。');
      return;
    }
    if (mutationPending) {
      pollTimer = window.setTimeout(poll, 2000);
      return;
    }
    polling = true;
    const startingRevision = revision;
    try {
      const next = await window.pywebview.api.get_state();
      if (startingRevision === revision) acceptState(next);
    } catch (error) {
      if (startingRevision === revision) {
        ready = false;
        bridgeError('无法获取桌面服务状态，正在自动重试。');
        render();
      }
    } finally {
      polling = false;
      pollTimer = window.setTimeout(poll, 2000);
    }
  }

  async function mutate(method, args = [], options = {}) {
    if (mutationPending) return { ok: false, message: '请等待当前操作完成。' };
    if (!window.pywebview?.api || !ready) {
      const message = '桌面服务尚未连接，请稍后重试。';
      toast(message, 'error');
      return { ok: false, message };
    }
    mutationPending = true;
    revision += 1;
    render();
    try {
      const api = window.pywebview.api;
      if (typeof api[method] !== 'function') throw new Error('此操作暂不可用，请重启应用。');
      const result = await api[method](...args);
      if (!result || typeof result.ok !== 'boolean') throw new Error('桌面服务未正确响应此操作。');
      if (result.state) acceptState(result.state);
      if (!result.ok) {
        toast(result.message || '操作失败，请重试。', 'error');
      } else if (!options.quiet && result.message) {
        toast(result.message);
      }
      return result;
    } catch (error) {
      const message = error?.message || '操作失败，请检查桌面服务。';
      toast(message, 'error');
      return { ok: false, message };
    } finally {
      mutationPending = false;
      revision += 1;
      render();
    }
  }

  function showFormError(id, message = '') {
    $(id).textContent = message;
    $(id).hidden = !message;
  }

  function showDialog(dialog) {
    if (!canInteract() || dialogs.some((item) => item.open)) return;
    dialog.showModal();
  }

  function clearAccountForm() {
    $('account-password').value = '';
    $('account-password').type = 'password';
    $('account-form').reset();
    editingAccountId = null;
  }

  function closeDialog(dialog) {
    // Clear sensitive input synchronously, before the queued native close event.
    if (dialog.id === 'account-dialog') clearAccountForm();
    dialog.close();
  }

  function openAccountDialog(account = null) {
    if (!canInteract()) return;
    if (!account && state.accounts.length >= accountLimit()) {
      toast(`最多支持 ${accountLimit()} 个账号，请先编辑或删除已有账号。`, 'warning');
      return;
    }
    $('account-form').reset();
    editingAccountId = account?.id ?? null;
    $('account-dialog-title').textContent = account ? '编辑账号' : '添加账号';
    $('account-user-id').value = account?.user_id || '';
    $('account-password').value = '';
    $('account-password').type = 'password';
    $('account-password').required = !account || !!account.decrypt_error;
    $('account-password').placeholder = account && !account.decrypt_error ? '留空以保留原密码' : '输入校园网密码';
    $('password-hint').textContent = account?.decrypt_error ? '原密码不可用，请输入新密码后保存。' : account ? '留空以保留原密码；新密码将在本机加密保存。' : '密码将在本机加密保存。';
    $('password-toggle').textContent = '显示';
    $('password-toggle').setAttribute('aria-pressed', 'false');
    $('account-service').replaceChildren();
    (state.services || ['电信', '移动', '联通', '校园网']).forEach((service) => {
      const option = document.createElement('option');
      option.value = service;
      option.textContent = service;
      $('account-service').append(option);
    });
    if (account) $('account-service').value = account.service;
    showFormError('account-form-error');
    showDialog($('account-dialog'));
    $('account-user-id').focus();
  }

  function openSettingsDialog() {
    if (!canInteract()) return;
    $('setting-auto-start').checked = !!state.settings.auto_start;
    $('setting-minimized').checked = !!state.settings.start_minimized;
    $('setting-notifications').checked = !!state.settings.show_notifications;
    $('setting-interval').value = state.settings.check_interval;
    $('tray-settings-note').hidden = state.tray_available;
    showFormError('settings-form-error');
    showDialog($('settings-dialog'));
  }

  function openDeleteDialog(account) {
    deletingAccountId = account.id;
    $('delete-description').textContent = `确定删除账号 ${account.user_id} 吗？${account.is_default ? '该账号是当前守护账号。' : ''}`;
    showFormError('delete-form-error');
    showDialog($('delete-dialog'));
  }

  async function submitDialog(event, method, args, errorId) {
    event.preventDefault();
    if (dialogSubmitting) return;
    const dialog = event.currentTarget.closest('dialog');
    dialogSubmitting = true;
    Array.from(dialog.querySelectorAll('button, input, select')).forEach((element) => { element.disabled = true; });
    const submitButton = dialog.querySelector('[type=submit]');
    submitButton.classList.add('is-loading');
    showFormError(errorId);
    try {
      const result = await mutate(method, args);
      if (result.ok) closeDialog(dialog);
      else showFormError(errorId, result.message || '操作失败，请重试。');
    } finally {
      Array.from(dialog.querySelectorAll('button, input, select')).forEach((element) => { element.disabled = false; });
      submitButton.classList.remove('is-loading');
      dialogSubmitting = false;
      render();
    }
  }

  async function uninstallApp() {
    if (!canInteract() || !state.can_uninstall || state.uninstall_pending || dialogSubmitting) return;
    const dialog = $('settings-dialog');
    const button = $('uninstall-button');
    dialogSubmitting = true;
    uninstallRequestPending = true;
    Array.from(dialog.querySelectorAll('button, input, select')).forEach((element) => { element.disabled = true; });
    button.classList.add('is-loading');
    showFormError('settings-form-error');
    try {
      const result = await mutate('uninstall_app', [], { quiet: true });
      if (!result.ok) showFormError('settings-form-error', result.message || '无法启动卸载，请稍后重试。');
    } finally {
      Array.from(dialog.querySelectorAll('button, input, select')).forEach((element) => { element.disabled = false; });
      button.classList.remove('is-loading');
      uninstallRequestPending = false;
      dialogSubmitting = false;
      render();
    }
  }

  $('login-button').addEventListener('click', () => mutate('login'));
  $('logout-button').addEventListener('click', () => mutate('logout'));
  $('tray-button').addEventListener('click', () => mutate('hide_window', [], { quiet: true }));
  $('quit-button').addEventListener('click', () => mutate('quit_app', [], { quiet: true }));
  $('settings-nav').addEventListener('click', openSettingsDialog);
  $('uninstall-button').addEventListener('click', uninstallApp);
  $('clear-logs-button').addEventListener('click', () => mutate('clear_logs'));
  $('logs-viewport').addEventListener('scroll', () => {
    const viewport = $('logs-viewport');
    if (currentPage === 'logs') followLogTail = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop < 40;
  });
  window.addEventListener('hashchange', navigate);
  $('add-account-button').addEventListener('click', () => openAccountDialog());
  $('empty-add-button').addEventListener('click', () => openAccountDialog());
  $('daemon-switch').addEventListener('change', async (event) => {
    const enabled = event.target.checked;
    await mutate('toggle_daemon', [enabled]);
    render();
  });
  $('retry-button').addEventListener('click', poll);
  $('password-toggle').addEventListener('click', () => {
    const shown = $('account-password').type === 'password';
    $('account-password').type = shown ? 'text' : 'password';
    $('password-toggle').textContent = shown ? '隐藏' : '显示';
    $('password-toggle').setAttribute('aria-pressed', String(shown));
  });
  $('account-form').addEventListener('submit', (event) => {
    const payload = {
      id: editingAccountId,
      user_id: $('account-user-id').value.trim(),
      password: $('account-password').value,
      service: $('account-service').value,
      is_default: editingAccountId === state.selected_account_id || state.accounts.length === 0,
    };
    if (!payload.user_id) {
      event.preventDefault();
      showFormError('account-form-error', '请输入校园网账号。');
      $('account-user-id').focus();
      return;
    }
    submitDialog(event, 'save_account', [payload], 'account-form-error').finally(() => { payload.password = ''; });
  });
  $('settings-form').addEventListener('submit', (event) => {
    if (state?.uninstall_pending) {
      event.preventDefault();
      return;
    }
    const interval = Number($('setting-interval').value);
    if (!Number.isInteger(interval) || interval < 10 || interval > 300) {
      event.preventDefault();
      showFormError('settings-form-error', '检测间隔应为 10–300 秒之间的整数。');
      $('setting-interval').focus();
      return;
    }
    submitDialog(event, 'update_settings', [{
      auto_start: $('setting-auto-start').checked,
      start_minimized: $('setting-minimized').checked,
      show_notifications: $('setting-notifications').checked,
      check_interval: interval,
    }], 'settings-form-error');
  });
  $('delete-form').addEventListener('submit', (event) => submitDialog(event, 'delete_account', [deletingAccountId], 'delete-form-error'));
  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = button.closest('dialog');
      if (!dialogSubmitting && !(dialog.id === 'settings-dialog' && state?.uninstall_pending)) closeDialog(dialog);
    });
  });
  dialogs.forEach((dialog) => {
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      if (!dialogSubmitting && !(dialog.id === 'settings-dialog' && state?.uninstall_pending)) closeDialog(dialog);
    });
  });
  $('account-dialog').addEventListener('close', () => {
    if (!$('account-dialog').open) clearAccountForm();
  });
  $('delete-dialog').addEventListener('close', () => {
    if (!$('delete-dialog').open) deletingAccountId = null;
  });
  window.addEventListener('pywebviewready', poll);
  navigate();
  if (window.pywebview?.api) poll();
  window.setTimeout(() => {
    if (!receivedFirstState && !window.pywebview?.api) {
      bridgeError('桌面服务尚未连接。请通过应用启动，直接打开网页无法管理账号。');
      $('accounts-placeholder-title').textContent = '等待桌面服务';
      $('accounts-placeholder-description').textContent = '连接后将显示本机保存的账号。';
    }
  }, 10000);
})();
