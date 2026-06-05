let smoothCpuPercent = null;
let maintenanceInProgress = false;
function getToken() {
  return localStorage.getItem("wgApiToken") || "";
}

function saveToken() {
  const token = document.getElementById("token").value.trim();
  localStorage.setItem("wgApiToken", token);
  loadAll();
  setTimeout(() => {
    const savedTab = localStorage.getItem("activeTab");
    showTab(["dashboardTab", "clientsTab", "activityTab"].includes(savedTab) ? savedTab : "dashboardTab");
    applyLang();
  }, 100);
}

async function api(path, options = {}) {
  const token = getToken();

  const res = await fetch(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      "X-API-Token": token,
    },
  });

  if (!res.ok) {
    const text = await res.text();
    if (res.status === 401) {
      showLoginOverlay();
    }
    throw new Error(`${res.status}: ${text}`);
  }

  return res;
}


function showLoginOverlay() {
  const el = document.getElementById("loginOverlay");
  if (el) el.style.display = "flex";
}

function hideLoginOverlay() {
  const el = document.getElementById("loginOverlay");
  if (el) el.style.display = "none";
}

function saveLoginToken() {
  const input = document.getElementById("loginTokenInput");
  const token = input ? input.value.trim() : "";

  if (!token) {
    showToast("API token не задан");
    return;
  }

  localStorage.setItem("wgApiToken", token);
  const settingsToken = document.getElementById("token");
  if (settingsToken) settingsToken.value = token;

  hideLoginOverlay();
  refreshAll();
}

function setVpnStatus(ok) {
  const el = document.getElementById("vpnStatus");
  if (!el) return;

  el.classList.remove("vpn-ok", "vpn-bad", "vpn-unknown");

  if (ok) {
    el.classList.add("vpn-ok");
    el.textContent = "●";
    el.title = getLang() === "ru" ? "VPN подключён" : "VPN connected";
  } else {
    el.classList.add("vpn-bad");
    el.textContent = "●";
    el.title = getLang() === "ru" ? "VPN не подключён или API недоступен" : "VPN disconnected or API unavailable";
  }
}

async function loadAll() {
  if (document.hidden) return;

  const tokenInput = document.getElementById("token");
  if (tokenInput) tokenInput.value = getToken();

  try {
    const [dash, peers, trafficHistory, activity] = await Promise.all([
      api("/dashboard").then(r => r.json()),
      api("/peers").then(r => r.json()),
      api("/traffic/history").then(r => r.json()),
      api("/activity").then(r => r.json()),
    ]);

    setVpnStatus(true);
    renderDashboard(dash);
    renderPeers(peers.peers);
    renderTrafficHistory(trafficHistory);
    loadTrafficHistoryPeriod();
    renderActivity(activity.events || []);
    applyLang();
  } catch (e) {
    setVpnStatus(false);
    document.getElementById("dashboard").innerHTML =
      `<b>Error:</b><br><pre>${escapeHtml(e.message)}</pre>`;
  }
}


function getLocationLabel(hostname) {
  hostname = String(hostname || "").toLowerCase();
  const ru = getLang() === "ru";

  if (hostname.startsWith("ams")) {
    return ru ? "🇳🇱 Амстердам" : "🇳🇱 Amsterdam";
  }

  if (hostname.startsWith("fra")) {
    return ru ? "🇩🇪 Франкфурт" : "🇩🇪 Frankfurt";
  }

  if (hostname.startsWith("waw")) {
    return ru ? "🇵🇱 Варшава" : "🇵🇱 Warsaw";
  }

  if (hostname.startsWith("lon")) {
    return ru ? "🇬🇧 Лондон" : "🇬🇧 London";
  }

  if (hostname.startsWith("hel")) {
    return ru ? "🇫🇮 Хельсинки" : "🇫🇮 Helsinki";
  }

  if (hostname.startsWith("sto")) {
    return ru ? "🇸🇪 Стокгольм" : "🇸🇪 Stockholm";
  }

  return ru ? "🌍 Неизвестно" : "🌍 Unknown";
}


function usageClass(percent) {
  percent = Number(percent) || 0;
  if (percent >= 80) return "usage-high";
  if (percent >= 60) return "usage-medium";
  return "usage-low";
}

function renderTopUserNow(w) {
  const top = w && w.top_user_now;

  if (!top) return "";

  if (top.active) {
    return `
      <div class="meta">
        ⚠️ ${t("topUserNow")}: <b>${escapeHtml(top.name || "-")}</b> • ${t("today")}: <b>${escapeHtml(top.today_total_human || "0 B")}</b>
        ↓ ${top.rx_mbps || 0} Mbps
        ↑ ${top.tx_mbps || 0} Mbps
      </div>
    `;
  }

  const last = top.last_event;

  if (!last || !last.name) {
    return "";
  }

  const minutes = Math.max(
    0,
    Math.floor((Date.now() / 1000 - (last.ts || 0)) / 60)
  );

  let ago;
  if (minutes < 1) {
    ago = getLang() === "ru" ? "только что" : "just now";
  } else if (minutes < 60) {
    ago = getLang() === "ru" ? `${minutes} мин назад` : `${minutes} min ago`;
  } else if (minutes < 1440) {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    ago = getLang() === "ru"
      ? (m ? `${h} ч ${m} мин назад` : `${h} ч назад`)
      : (m ? `${h}h ${m}m ago` : `${h}h ago`);
  } else {
    const d = Math.floor(minutes / 1440);
    const h = Math.floor((minutes % 1440) / 60);
    ago = getLang() === "ru"
      ? (h ? `${d} д ${h} ч назад` : `${d} д назад`)
      : (h ? `${d}d ${h}h ago` : `${d}d ago`);
  }

  return `
    <div class="meta">
      🕒 ${t("lastTopUser")}: <b>${escapeHtml(last.name)}</b>
      (${last.total_mbps || 0} Mbps) • ${ago}
    </div>
  `;
}


function renderDashboard(d) {
  const w = d.wireguard;

  document.getElementById("dashboard").innerHTML = `
    <div><b>${escapeHtml(d.display_name || "Family VPN")}</b></div><div class="meta">${getLocationLabel(d.hostname)} (${escapeHtml(d.hostname)})</div>
    <div class="meta">${t("uptime")}: ${escapeHtml(d.uptime.human)}</div>
    ${(() => {
      const rawCpu = Number(d.cpu?.percent || 0);
      smoothCpuPercent = smoothCpuPercent === null ? rawCpu : (smoothCpuPercent * 0.85 + rawCpu * 0.15);
      const cpu = Math.round(smoothCpuPercent);
      return `<div class="meta">CPU: <span class="${usageClass(cpu)}">${cpu}%</span></div>`;
    })()}
    <div class="meta">${t("ram")}: <span class="${usageClass(d.memory.used_percent)}">${d.memory.used_percent}%</span></div>
    <div class="meta">${t("disk")}: <span class="${usageClass(d.disk_root.used_percent)}">${d.disk_root.used_percent}%</span></div>
    <hr>
    <div>${t("clientsCount")}: <b>${w.peer_count}</b></div>
    <div>${t("connected")}: <b>${w.online_peer_count}</b> / ${t("active")}: <b class="online">${w.top_user_now?.active_peer_count || 0}</b></div>
    <hr>
    <div>${t("today")}: <b>${escapeHtml(w.vpn_today_human || "0.00 B")}</b></div>
    <div>${t("week")}: <b>${escapeHtml(w.vpn_week_human || "0.00 B")}</b></div>
    <div>${t("month")}: <b>${escapeHtml(w.vpn_month_human || "0.00 B")}</b></div>
    <div>${t("total")}: <b>${escapeHtml(w.vpn_saved_total_human || "0.00 B")}</b></div>
    ${renderTopUserNow(w)}
  `;
}

function renderPeers(peers) {
  const el = document.getElementById("peers");

  peers.sort((a, b) => {
    const trafficValue = p => {
      const period = getTrafficPeriod();
      if (period === "today") return p.today_total_bytes || 0;
      if (period === "week") return p.week_total_bytes || 0;
      return p.saved_total_bytes || p.transfer_total_bytes || 0;
    };

    const groupValue = p => {
      if (p.is_active_now) return 3;
      if (p.online) return 2;
      return 1;
    };

    return groupValue(b) - groupValue(a)
      || trafficValue(b) - trafficValue(a)
      || String(a.name || "").localeCompare(String(b.name || ""));
  });

  el.innerHTML = peers.map(p => {
    const statusClass = !p.enabled ? "status-disabled" : (p.online ? "status-online" : "status-offline");
    const statusText = !p.enabled ? t("disabled") : (p.online ? t("online") : t("offline"));
    const statusIcon = !p.enabled ? "🔴" : (p.online ? "🟢" : "⚪");

    return `
    <div class="peer ${statusClass}">
      <div class="name peer-title-row">
        <span>
          <span>${statusIcon}</span>
          ${escapeHtml(p.name)}
          ${p.protected ? " 🔒" : ""}
          <button
            class="turtle-name-button ${p.speed_limited ? 'turtle-hot' : ''}"
            title="${p.speed_limited ? t('normalSpeed') : t('slowMode')}"
            onclick="${p.speed_limited
              ? `setNormalSpeed('${p.client_id}','${escapeAttr(p.name)}')`
              : `setSlowSpeed('${p.client_id}','${escapeAttr(p.name)}')`}">
            🐢
          </button>
        </span>
        ${p.is_active_now ? '<span class="peer-activity-dot" title="Active now">●</span>' : ''}
      </div>
      <div class="meta">${t("lastSeen")}: ${escapeHtml(formatHandshakeDate(p.latest_handshake || p.saved_last_seen))}</div>
      <div class="meta">${t("onlineToday")}: ${escapeHtml(p.online_today_human || "0s")}</div>
      <div class="meta">${trafficPeriodLabel()}: ${
  escapeHtml(
    getTrafficPeriod() === "today"
      ? p.today_total_human
      : getTrafficPeriod() === "week"
        ? p.week_total_human
        : (p.saved_total_human || p.transfer_total_human)
  )
}</div>

      <div class="peer-actions">
        <div class="peer-actions-left">
          <button class="secondary" onclick="showConfig('${p.client_id}')">${t("config")}</button>
          <button class="secondary" onclick="showQR('${p.client_id}')">QR</button>
        </div>
        <div class="peer-actions-right">
          ${p.enabled
            ? `<button class="secondary"
                onclick="disablePeer('${p.client_id}','${escapeAttr(p.name)}')"
                ${p.protected ? "disabled" : ""}>
                ${t("disable")}
               </button>`
            : `<button
                onclick="enablePeer('${p.client_id}','${escapeAttr(p.name)}')">
                ${t("enable")}
               </button>`
          }
        </div>
      </div>
    </div>
  `;
  }).join("");
}

function renderActivity(events) {
  const el = document.getElementById("activity");

  if (!events.length) {
    el.innerHTML = '<div class="meta">No activity yet</div>';
    return;
  }

  el.innerHTML = events.map(e => {
    const icon =
      e.action === "enable" ? "✅" :
      e.action === "disable" ? "❌" :
      "ℹ️";

    let ts = "";
    try {
      ts = new Date(e.ts).toLocaleString(getLang() === "ru" ? "ru-RU" : "en-US", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      });
    } catch (err) {
      ts = (e.ts || "").replace("T", " ").replace("Z", "");
    }

    return `
      <div class="activity-item">
        ${icon}
        <b>${escapeHtml(e.peer || "-")}</b>
        ${escapeHtml(e.action)}
        <div class="meta">${escapeHtml(ts)}</div>
      </div>
    `;
  }).join("");
}

async function showConfig(id) {
  try {
    const text = await (await api(`/peer/${id}/config`)).text();
    showModal(`<h3>${t("config")}</h3><pre>${escapeHtml(text)}</pre>`);
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

async function showQR(id) {
  try {
    const res = await api(`/peer/${id}/qr`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    showModal(`<h3>QR</h3><img class="qr" src="${url}">`);
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

async function setSlowSpeed(id, name) {

  try {
    await (await api(`/peer/${id}/speed-limit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ rate: "256kbit" }),
    })).json();

    toast(`🐢 ${name}: ${t("slowMode")}`);
    loadAll();
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

async function setNormalSpeed(id, name) {

  try {
    await (await api(`/peer/${id}/speed-normal`, {
      method: "POST",
    })).json();

    toast(`🚀 ${name}: ${t("normalSpeed")}`);
    loadAll();
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

async function enablePeer(id, name) {
  if (!confirm(`${t("enable")} ${name}?`)) return;

  try {
    await (
      await api(`/peer/${id}/enable`, {
        method: "POST"
      })
    ).json();

    toast(getLang() === "ru" ? `✅ ${name} включён` : `✅ ${name} enabled`);
    loadAll();
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

async function disablePeer(id, name) {
  if (!confirm(`${t("disable")} ${name}?`)) return;

  try {
    const check = await (await api(`/peer/${id}/disable-check`, { method: "POST" })).json();

    if (!check.allowed) {
      showModal(`<h3>Cannot disable</h3><pre>${escapeHtml(JSON.stringify(check, null, 2))}</pre>`);
      return;
    }

    await (await api(`/peer/${id}/disable`, { method: "POST" })).json();

    toast(getLang() === "ru" ? `⏸ ${name} приостановлен` : `⏸ ${name} paused`);
    loadAll();
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

function showModal(html, modalClass = "") {
  const modal = document.getElementById("modal");
  const content = document.getElementById("modalContent");
  if (!modal || !content) return;

  modal.className = modalClass || "";
  content.innerHTML = html;
  modal.showModal();
}

function closeModal() {
  const modal = document.getElementById("modal");
  if (!modal) return;

  modal.close();
  modal.className = "";

  const content = document.getElementById("modalContent");
  if (content) content.innerHTML = "";
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.display = "block";

  setTimeout(() => {
    el.style.display = "none";
  }, 2500);
}

function showTab(tabId) {
  const mainTabsIds = ["dashboardTab", "clientsTab", "activityTab"];
  const allTabsIds = ["dashboardTab", "clientsTab", "activityTab", "settingsTab"];

  const mainTabs = document.querySelector(".tabs");
  if (mainTabs) {
    mainTabs.style.display = tabId === "settingsTab" ? "none" : "flex";
  }

  allTabsIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = id === tabId ? "block" : "none";
  });

  const settingsButton = document.getElementById("settingsButton");
  if (settingsButton) {
    settingsButton.classList.toggle("settings-active", tabId === "settingsTab");
  }

  document.querySelectorAll(".tabs button").forEach(btn => {
    btn.classList.remove("active-tab");
  });

  const tabButtonMap = {
    dashboardTab: 0,
    clientsTab: 1,
    activityTab: 2,
  };

  if (mainTabsIds.includes(tabId)) {
    localStorage.setItem("activeTab", tabId);
    localStorage.setItem("settingsOpen", "0");

    const idx = tabButtonMap[tabId];
    const buttons = document.querySelectorAll(".tabs button");
    if (buttons[idx]) buttons[idx].classList.add("active-tab");
  } else if (tabId === "settingsTab") {
    localStorage.setItem("settingsOpen", "1");
  }
}

function formatHandshakeDate(ts) {
  ts = Number(ts) || 0;

  if (!ts) {
    return getLang() === "ru" ? "Никогда" : "Never";
  }

  const d = new Date(ts * 1000);
  const now = new Date();

  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());

  const time = d.toLocaleTimeString(
    getLang() === "ru" ? "ru-RU" : "en-GB",
    {
      hour: "2-digit",
      minute: "2-digit"
    }
  );

  if (target.getTime() === today.getTime()) {
    return (getLang() === "ru" ? "Сегодня " : "Today ") + time;
  }

  if (target.getTime() === yesterday.getTime()) {
    return (getLang() === "ru" ? "Вчера " : "Yesterday ") + time;
  }

  return d.toLocaleString(
    getLang() === "ru" ? "ru-RU" : "en-GB",
    {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    }
  );
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(s) {
  return String(s).replaceAll("'", "\\'");
}

loadAll();


setInterval(() => {
  if (!document.hidden) loadAll();
}, 30000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadAll();
});

function showCreateClient() {
  showModal(`
    <h3>${t("newClient")}</h3>
    <input id="newClientName" class="create-client-input" type="text" placeholder="${t("clientName")}" autocomplete="off">
    <button class="create-client-button" onclick="createClient()">${t("create")}</button>
  `, "create-client-modal");

  const input = document.getElementById("newClientName");
  if (input) {
    input.value = "";
    setTimeout(() => input.focus(), 100);
  }
}

function closeCreateOverlay() {
  closeModal();
}

async function createClient(nameArg) {
  const input = document.getElementById("newClientName");
  const name = nameArg ? String(nameArg).trim() : (input ? input.value.trim() : "");

  if (!name) {
    toast(getLang() === "ru" ? "Введите имя клиента" : "Client name is required");
    return;
  }

  try {
    const result = await (
      await api("/peer/create", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ name }),
      })
    ).json();

    closeCreateOverlay();

    toast(getLang() === "ru" ? `✅ ${result.peer} создан` : `✅ ${result.peer} created`);
    await loadAll();

    setTimeout(() => {
      showQR(result.client_id);
    }, 400);
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}


function showSettings() {
  showModal(`
    <div class="settings-modal">
      <h3>${t("settings")}</h3>

      <label class="meta">${t("apiToken")}</label>
      <div class="row">
        <input id="settingsToken" type="password"
               placeholder="${t("apiToken")}"
               value="${escapeHtml(getToken())}">
        <button onclick="saveSettingsToken()">${t("save")}</button>
      </div>

      <div class="meta">${t("tokenStored")}</div>

      <hr>

      <h3>${t("language")}</h3>
      <div class="row">
        <button onclick="setLang('en')">English</button>
        <button onclick="setLang('ru')">Русский</button>
      </div>
    </div>
  `);
}

function saveSettingsToken() {
  const tokenInput = document.getElementById("settingsToken");
  const token = tokenInput ? tokenInput.value.trim() : "";

  localStorage.setItem("wgApiToken", token);

  closeModal();

  toast(getLang() === "ru" ? "✅ Токен сохранён" : "✅ Token saved");

  loadAll();
}

async function showDeleteClient() {
  try {
    const data = await (await api("/peers")).json();

    const items = data.peers
      .map(p => `
        <div class="delete-row">
          <div>
            <b>${escapeHtml(p.name)}</b>
            <div class="meta">${escapeHtml(p.ip || "-")}</div>
          </div>
          <button class="danger" onclick="deleteClientById('${p.client_id}', '${escapeAttr(p.name)}')">${t("delete")}</button>
        </div>
      `)
      .join("");

    showModal(`
      <h3>${t("deleteClient")}</h3>
      <div class="delete-list">
        ${items}
      </div>
    `);
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

async function deleteClientById(id, name) {
  if (!id) return;

  if (!confirm(`${t("delete")} ${name}?`)) return;

  try {
    await api(`/peer/${id}`, {
      method: "DELETE"
    });

    closeModal();
    toast(getLang() === "ru" ? `🗑 ${name} удалён` : `🗑 ${name} deleted`);
    loadAll();
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}


const I18N = {
  en: {
    status: "Status",
    clients: "Clients",
    logs: "Logs",
    settings: "Settings",
    settingsGeneral: "General",
    settingsBackups: "Backups",
    settingsSecurity: "Security",
    settingsSystem: "System",
    backups: "Backups",
    backupHint: "Backup includes clients, keys, settings, statistics, backend and PWA.",
    createNow: "Create now",
    download: "Download",
    downloadLatest: "Download latest",
    downloadPrevious: "Download previous",
    latestBackup: "Latest backup",
    previousBackup: "Previous backup",
    size: "Size",
    none: "none",
    backupLoadFailed: "Failed to load backup information.",
    backupCreating: "Creating backup...",
    backupCreated: "Backup created",
    backupCreateFailed: "Failed to create backup",
    restore: "Restore",
    restoreConfirm: "Restore from this backup? Current state will be saved before restore. Services will restart.",
    restoreStarted: "Restore started, waiting for restart...",
    restoreCountdown: "Reloading in {n}s...",
    restoreFailed: "Failed to restore backup",
    apiTokenMissing: "API token is missing",
    loginHint: "Enter your API token to connect.",
    loading: "Loading...",
    maintenance: "Maintenance",
    restartVpnServer: "Restart VPN Server",
    restartControlPanel: "Restart Control Panel",
    rebootVps: "Reboot VPS",
    maintenanceHint: "VPN connection may be interrupted.",
    serverName: "Server Name",
    trafficPeriod: "Traffic Period",
    trafficHistory: "Traffic History",
    activityLog: "Activity Log",
    noData: "No data",
    today: "Today",
    week: "Week",
    month: "Month",
    total: "Total",
    save: "Save",
    language: "Language",
    apiToken: "API Token",
    tokenStored: "Token is stored only in this browser.",
    newClient: "+ New Client",
    create: "Create",
    clientName: "Client name",
    deleteClient: "Delete Client",
    delete: "Delete",
    close: "Close",
    slowMode: "Slow mode",
    normalSpeed: "Normal speed",
    config: "Config",
    enable: "Enable",
    disable: "Disable",
    qr: "QR",
    online: "Online",
    offline: "Offline",
    disabled: "Disabled",
    enabled: "Enabled",
    yes: "yes",
    no: "no",
    uptime: "Uptime",
    load: "Load",
    cpu: "CPU",
    ram: "RAM",
    disk: "Disk",
    clientsCount: "Clients",
    connected: "Online",
    active: "Active",
    topUserNow: "Top user now",
    lastTopUser: "Last high activity",
    totalTraffic: "Total traffic",
    lastSeen: "Last",
    onlineToday: "Online today",
    traffic: "Traffic",
    trafficToday: "Traffic today",
    trafficWeek: "Traffic this week",
    trafficTotal: "Traffic total"
  },
  ru: {
    status: "Статус",
    clients: "Клиенты",
    logs: "Логи",
    settings: "Настройки",
    settingsGeneral: "Основные",
    settingsBackups: "Резервные копии",
    settingsSecurity: "Безопасность",
    settingsSystem: "Система",
    backups: "Резервные копии",
    backupHint: "Резервная копия содержит клиентов, ключи, настройки, статистику, backend и PWA.",
    createNow: "Создать сейчас",
    download: "Скачать",
    downloadLatest: "Скачать последнюю",
    downloadPrevious: "Скачать предыдущую",
    latestBackup: "Последняя копия",
    previousBackup: "Предыдущая копия",
    size: "Размер",
    none: "нет",
    backupLoadFailed: "Не удалось загрузить сведения о резервных копиях.",
    backupCreating: "Создаю резервную копию...",
    backupCreated: "Резервная копия создана",
    backupCreateFailed: "Не удалось создать резервную копию",
    restore: "Восстановить",
    restoreConfirm: "Восстановить из этой резервной копии? Текущее состояние будет сохранено перед восстановлением. Сервисы будут перезапущены.",
    restoreStarted: "Восстановление запущено, ожидаю перезапуска...",
    restoreCountdown: "Перезагрузка через {n}с...",
    restoreFailed: "Не удалось восстановить резервную копию",
    apiTokenMissing: "API token не задан",
    loginHint: "Введите API token для подключения к панели.",
    loading: "Загрузка...",
    maintenance: "Обслуживание",
    restartVpnServer: "Перезапустить VPN-сервер",
    restartControlPanel: "Перезапустить панель управления",
    rebootVps: "Перезагрузить VPS",
    maintenanceHint: "VPN-соединение может быть прервано.",
    serverName: "Имя сервера",
    trafficPeriod: "Период трафика",
    trafficHistory: "История трафика",
    activityLog: "Журнал действий",
    noData: "Нет данных",
    today: "Сегодня",
    week: "Неделя",
    month: "Месяц",
    total: "Всего",
    save: "Сохранить",
    language: "Язык",
    apiToken: "API-токен",
    tokenStored: "Токен хранится только в этом браузере.",
    newClient: "+ Новый клиент",
    create: "Создать",
    clientName: "Имя клиента",
    deleteClient: "Удалить клиента",
    delete: "Удалить",
    close: "Закрыть",
    slowMode: "Медленно",
    normalSpeed: "Обычная скорость",
    config: "Конфиг",
    enable: "Включить",
    disable: "Отключить",
    qr: "QR",
    online: "Онлайн",
    offline: "Офлайн",
    disabled: "Отключен",
    enabled: "Включен",
    yes: "да",
    no: "нет",
    uptime: "Время работы",
    load: "Нагрузка",
    cpu: "CPU",
    ram: "ОЗУ",
    disk: "Диск",
    clientsCount: "Клиенты",
    connected: "Подключено",
    active: "Активно",
    topUserNow: "Сейчас активнее всех",
    lastTopUser: "Последняя высокая активность",
    totalTraffic: "Общий трафик",
    lastSeen: "Последнее подключение",
    onlineToday: "В сети сегодня",
    traffic: "Трафик",
    trafficToday: "Трафик за сегодня",
    trafficWeek: "Трафик за неделю",
    trafficTotal: "Трафик всего"
  }
};

function getLang() {
  return localStorage.getItem("wgLang") || "en";
}

function setLang(lang) {
  localStorage.setItem("wgLang", lang);
  closeModal();
  applyLang();
  loadTrafficPeriod();
  loadAll();
  toast(lang === "ru" ? "✅ Язык сохранён" : "✅ Language saved");
}


function t(key) {
  return (I18N[getLang()] || I18N.en)[key] || key;
}

function applyLang() {
  document.documentElement.lang = getLang();

  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });

  if (typeof loadTrafficPeriod === "function") {
    loadTrafficPeriod();
  }
  if (typeof loadTrafficHistoryPeriod === "function") {
    loadTrafficHistoryPeriod();
  }
}

let previousTab = "dashboardTab";

function toggleSettings() {
  const settingsTab = document.getElementById("settingsTab");

  if (settingsTab && settingsTab.style.display === "block") {
    const previous = localStorage.getItem("previousMainTab") || localStorage.getItem("activeTab") || "dashboardTab";
    showTab(previous === "settingsTab" ? "dashboardTab" : previous);
    return;
  }

  const currentMainTab = localStorage.getItem("activeTab") || "dashboardTab";
  localStorage.setItem("previousMainTab", currentMainTab === "settingsTab" ? "dashboardTab" : currentMainTab);
  showTab("settingsTab");

  let savedSubtab = localStorage.getItem("settingsSubtab") || "settingsGeneral";
  if (!["settingsGeneral", "settingsBackups", "settingsSecurity"].includes(savedSubtab)) {
    savedSubtab = "settingsGeneral";
  }
  if (typeof showSettingsSubtab === "function") {
    showSettingsSubtab(savedSubtab);
  }

  loadSettings();
}


async function loadSettings() {
  try {
    const settings = await (await api("/settings")).json();
    const input = document.getElementById("serverNameInput");
    if (input) input.value = settings.display_name || "";
  } catch (e) {
    // ignore
  }
}

async function saveServerName() {
  const input = document.getElementById("serverNameInput");
  const displayName = input ? input.value.trim() : "";

  if (!displayName) {
    toast(getLang() === "ru" ? "Введите имя сервера" : "Name is required");
    return;
  }

  try {
    await api("/settings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ display_name: displayName }),
    });

    toast(getLang() === "ru" ? "✅ Имя сервера сохранено" : "✅ Server name saved");
    loadAll();
    loadSettings();
    loadTrafficHistoryPeriod();
  } catch (e) {
    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}

function getTrafficPeriod() {
  return localStorage.getItem("trafficPeriod") || "today";
}

function saveTrafficPeriod() {
  const v = document.getElementById("trafficPeriod").value;

  localStorage.setItem("trafficPeriod", v);

  loadAll();

  toast(
    getLang() === "ru"
      ? "✅ Период трафика сохранён"
      : "✅ Traffic period saved"
  );
}

function loadTrafficPeriod() {
  const el = document.getElementById("trafficPeriod");
  if (el) {
    el.innerHTML = `
      <option value="today">${t("today")}</option>
      <option value="week">${t("week")}</option>
      <option value="total">${t("total")}</option>
    `;
    el.value = getTrafficPeriod();
  }
}

function renderTrafficHistory(data) {
  const el = document.getElementById("trafficHistory");
  if (!el) return;

  const period = getTrafficHistoryPeriod();
  const items = (data && data[period]) ? data[period] : [];

  const visible = items.filter(x => x.bytes > 0).slice(0, 10);

  if (!visible.length) {
    el.innerHTML = `<div class="meta">${t("noData")}</div>`;
    return;
  }

  el.innerHTML = visible.map((x, idx) => {
    const statusIcon = !x.enabled ? "🔴" : (x.online ? "🟢" : "⚪");

    return `
      <div class="traffic-history-row">
        <div>
          <b>${idx + 1}. ${statusIcon} ${escapeHtml(x.name)}</b>
          <div class="meta">
            ↓ ${escapeHtml(x.tx_human || "0.00 B")}
            &nbsp;&nbsp;
            ↑ ${escapeHtml(x.rx_human || "0.00 B")}
          </div>
        </div>
        <b>${escapeHtml(x.human || "0.00 B")}</b>
      </div>
    `;
  }).join("");
}




function trafficPeriodLabel() {
  const period = getTrafficPeriod();

  if (period === "today") return t("trafficToday");
  if (period === "week") return t("trafficWeek");

  return t("trafficTotal");
}


function getTrafficHistoryPeriod() {
  return localStorage.getItem("trafficHistoryPeriod") || "week";
}

function saveTrafficHistoryPeriod() {
  const el = document.getElementById("trafficHistoryPeriod");
  if (!el) return;

  localStorage.setItem("trafficHistoryPeriod", el.value);
  loadAll();
}

function loadTrafficHistoryPeriod() {
  const el = document.getElementById("trafficHistoryPeriod");
  if (!el) return;

  el.innerHTML = `
    <option value="week">${t("week")}</option>
    <option value="month">${t("month")}</option>
    <option value="total">${t("total")}</option>
  `;

  el.value = getTrafficHistoryPeriod();
}


function maintenanceMessage(path) {
  if (path.includes("restart-wg-easy")) {
    return getLang() === "ru"
      ? "🔄 VPN-сервер перезапускается"
      : "🔄 VPN Server is restarting";
  }

  if (path.includes("restart-wg-admin")) {
    return getLang() === "ru"
      ? "🔄 Панель управления перезапускается"
      : "🔄 Control Panel is restarting";
  }

  if (path.includes("reboot-server")) {
    return getLang() === "ru"
      ? "⚠️ Перезагрузка VPS запущена. Соединение скоро будет потеряно."
      : "⚠️ VPS reboot started. Connection will be lost shortly.";
  }

  return "✅ OK";
}

async function maintenancePost(path, message) {
  if (!confirm(message)) return;

  maintenanceInProgress = true;

  const okMessage = maintenanceMessage(path);
  toast(okMessage);

  const waitMs = path.includes("reboot-server")
    ? 120000
    : 25000;

  setTimeout(() => {
    maintenanceInProgress = false;
    loadAll();
  }, waitMs);

  try {
    await api(path, { method: "POST" });
  } catch (e) {
    if (maintenanceInProgress) {
      // During maintenance, temporary connection errors are expected.
      return;
    }

    showModal(`<pre>${escapeHtml(e.message)}</pre>`);
  }
}


function restartWgEasy() {
  maintenancePost(
    "/maintenance/restart-wg-easy",
    getLang() === "ru"
      ? "Перезапустить VPN-сервер? VPN-клиенты могут кратковременно переподключиться."
      : "Restart VPN Server? VPN clients may briefly reconnect."
  );
}

function restartWgAdmin() {
  maintenancePost(
    "/maintenance/restart-wg-admin",
    getLang() === "ru"
      ? "Перезапустить панель управления?"
      : "Restart Control Panel?"
  );
}

function rebootServer() {
  maintenancePost(
    "/maintenance/reboot-server",
    getLang() === "ru"
      ? "Перезагрузить VPS? Все VPN-клиенты будут временно отключены."
      : "Reboot VPS? All VPN clients will be temporarily disconnected."
  );
}

async function refreshAll() {
  const btn = document.getElementById("refreshButton");

  if (btn) {
    btn.style.opacity = "0.5";
  }

  try {
    if ("caches" in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map(key => caches.delete(key)));
    }

    await loadAll();

    toast(
      getLang() === "ru"
        ? "✓ Данные и кэш обновлены"
        : "✓ Data and cache refreshed"
    );

    const url = new URL(window.location.href);
    url.searchParams.set("v", Date.now().toString());
    window.history.replaceState(null, "", url.toString());
  } catch (e) {
    toast(
      getLang() === "ru"
        ? "⚠️ Ошибка обновления"
        : "⚠️ Refresh error"
    );
  } finally {
    if (btn) {
      btn.style.opacity = "1";
    }
  }
}


function showParentalControl() {
  showModal(`
    <h3>${getLang() === "ru" ? "Родительский контроль" : "Parental Control"}</h3>
    <div class="meta">
      ${getLang() === "ru"
        ? "Здесь будут лимиты по часу, дню, неделе и месяцу, расписания доступа и уведомления."
        : "Hourly, daily, weekly and monthly limits, access schedules and notifications will be here."}
    </div>
  `);
}

function showSettingsSubtab(id) {
  ["settingsGeneral", "settingsBackups", "settingsSecurity"].forEach(panelId => {
    const el = document.getElementById(panelId);
    if (el) el.style.display = panelId === id ? "block" : "none";
  });

  document.querySelectorAll(".settings-subtab").forEach(btn => {
    btn.classList.remove("active-tab");
  });

  const map = {
    settingsGeneral: 0,
    settingsBackups: 1,
    settingsSecurity: 2,
  };

  const buttons = document.querySelectorAll(".settings-subtab");
  const idx = map[id];
  if (buttons[idx]) buttons[idx].classList.add("active-tab");

  localStorage.setItem("settingsSubtab", id);
  if (id === "settingsBackups" && typeof loadBackupStatus === "function") {
    loadBackupStatus();
  }
}


function backupTime(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString([], {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  } catch (e) {
    return String(ts);
  }
}

function renderBackupInfo(item, title, kind) {
  if (!item || !item.exists) {
    return `
      <div class="backup-item">
        <div class="backup-item-main">
          <div><b>${title}</b></div>
          <div class="meta">${t("none")}</div>
        </div>
      </div>
    `;
  }

  return `
    <div class="backup-item">
      <div class="backup-item-main">
        <div><b>${title}</b></div>
        <div>${backupTime(item.mtime)}</div>
        <div class="meta">${t("size")}: ${escapeHtml(item.size_human || "")}</div>
      </div>
      <div style="display:flex; flex-direction:column; gap:8px; align-items:flex-end;">
        <button class="backup-download-btn" onclick="downloadBackup('${kind}')">${t("download")}</button>
        <button class="backup-download-btn" onclick="restoreBackup('${kind}')">${t("restore")}</button>
      </div>
    </div>
  `;
}

async function loadBackupStatus() {
  const el = document.getElementById("backupStatus");
  if (!el) return;

  try {
    const res = await api("/backup/status");
    const data = await res.json();

    el.innerHTML = `
      ${renderBackupInfo(data.latest, t("latestBackup"), "latest")}
      <hr>
      ${renderBackupInfo(data.previous, t("previousBackup"), "previous")}
    `;
  } catch (e) {
    el.innerHTML = t("backupLoadFailed");
  }
}

async function createBackupNow() {
  try {
    const el = document.getElementById("backupStatus");
    if (el) el.innerHTML = t("backupCreating");

    await api("/backup/create", { method: "POST" });
    await loadBackupStatus();

    showToast(t("backupCreated"));
  } catch (e) {
    showToast(t("backupCreateFailed"));
    await loadBackupStatus();
  }
}

function downloadBackup(kind) {
  const token = getToken();
  if (!token) {
    showLoginOverlay();
    return;
  }

  const url = `/backup/download/${kind}?token=${encodeURIComponent(token)}&t=${Date.now()}`;
  window.open(url, "_blank");
}

async function restoreBackup(kind) {
  if (!confirm(t("restoreConfirm"))) return;

  try {
    showToast(t("restoreStarted"));

    await api(`/backup/restore/${kind}`, { method: "POST" });

    let seconds = 5;
    function tick() {
      showToast(t("restoreCountdown").replace("{n}", seconds));
      if (seconds <= 0) {
        location.reload();
        return;
      }
      seconds--;
      setTimeout(tick, 1000);
    }
    tick();

  } catch (e) {
    showToast(t("restoreFailed"));
  }
}
