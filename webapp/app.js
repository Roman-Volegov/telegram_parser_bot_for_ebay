(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#1a1a1a");
      tg.setBackgroundColor("#1a1a1a");
    } catch (_) {
      // старые клиенты могут не принимать hex
    }
  }

  const state = {
    me: null,
    searches: [],
    logs: [],
  };

  const STATUS_LABELS = {
    ok: "ок",
    seed: "seed",
    empty: "пусто",
    error: "ошибка",
  };

  const els = {
    greeting: document.getElementById("greeting"),
    searchesList: document.getElementById("searches-list"),
    searchesEmpty: document.getElementById("searches-empty"),
    logsList: document.getElementById("logs-list"),
    logsEmpty: document.getElementById("logs-empty"),
    createSource: document.getElementById("create-source"),
    createMarketplace: document.getElementById("create-marketplace"),
    createMarketplaceWrap: document.getElementById("create-marketplace-wrap"),
    createBinWrap: document.getElementById("create-bin-wrap"),
    sourcesBox: document.getElementById("sources-box"),
    marketplace: document.getElementById("ebay-marketplace"),
    keysStatus: document.getElementById("keys-status"),
    ebayApiBlock: document.getElementById("ebay-api-block"),
    ebayChecklist: document.getElementById("ebay-checklist"),
    ebayClientId: document.getElementById("ebay-client-id"),
    ebayClientSecret: document.getElementById("ebay-client-secret"),
    deletionBox: document.getElementById("deletion-box"),
    deletionUrl: document.getElementById("deletion-url"),
    deletionToken: document.getElementById("deletion-token"),
    toast: document.getElementById("toast"),
    errorScreen: document.getElementById("error-screen"),
    errorText: document.getElementById("error-text"),
  };

  function initData() {
    return (tg?.initData || "").trim();
  }

  async function waitForInitData(timeoutMs = 2500) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (initData()) return initData();
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return initData();
  }

  async function api(path, options = {}) {
    const headers = {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData(),
      ...(options.headers || {}),
    };
    const res = await fetch(`/api${path}`, { ...options, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail || res.statusText || "Ошибка запроса";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => els.toast.classList.add("hidden"), 2400);
  }

  function showError(message) {
    els.errorText.textContent = message;
    els.errorScreen.classList.remove("hidden");
  }

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === name);
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `panel-${name}`);
    });
    if (name === "settings") syncEbayBlockVisibility();
    if (name === "logs") loadLogs().catch((err) => toast(err.message));
  }

  function selectedSources() {
    return [...els.sourcesBox.querySelectorAll("input:checked")].map((el) => el.value);
  }

  function syncEbayBlockVisibility() {
    const show = selectedSources().includes("ebay_api");
    els.ebayApiBlock.classList.toggle("hidden", !show);
  }

  function syncCreateSourceFields() {
    const source = els.createSource.value;
    const hideEbayFields = source === "poshmark" || source === "etsy";
    els.createBinWrap.classList.toggle("hidden", hideEbayFields);
    els.createMarketplaceWrap.classList.toggle("hidden", hideEbayFields);
  }

  function fillCreateMarketplace() {
    const labels = state.me.ebay_marketplace_labels || {};
    const markets = state.me.ebay_marketplaces || ["EBAY_US"];
    const current = state.me.ebay_marketplace || "EBAY_US";
    els.createMarketplace.innerHTML = "";
    markets.forEach((market) => {
      const opt = document.createElement("option");
      opt.value = market;
      opt.textContent = labels[market] || market;
      if (market === current) opt.selected = true;
      els.createMarketplace.appendChild(opt);
    });
  }

  function fillSettings() {
    const labels = state.me.source_labels || {};
    const enabled = new Set(state.me.enabled_sources || []);
    const marketLabels = state.me.ebay_marketplace_labels || {};

    els.createSource.innerHTML = "";
    enabled.forEach((source) => {
      const opt = document.createElement("option");
      opt.value = source;
      opt.textContent = labels[source] || source;
      els.createSource.appendChild(opt);
    });
    els.createSource.onchange = syncCreateSourceFields;
    fillCreateMarketplace();
    syncCreateSourceFields();

    els.sourcesBox.innerHTML = "";
    Object.entries(labels).forEach(([value, label]) => {
      const row = document.createElement("label");
      row.className = "source-item";
      row.innerHTML = `
        <input type="checkbox" value="${value}" ${enabled.has(value) ? "checked" : ""} />
        <span>${label}</span>
      `;
      els.sourcesBox.appendChild(row);
    });
    els.sourcesBox.querySelectorAll("input").forEach((input) => {
      input.addEventListener("change", syncEbayBlockVisibility);
    });

    els.marketplace.innerHTML = "";
    (state.me.ebay_marketplaces || ["EBAY_US"]).forEach((market) => {
      const opt = document.createElement("option");
      opt.value = market;
      opt.textContent = marketLabels[market] || market;
      if (market === (state.me.ebay_marketplace || "EBAY_US")) opt.selected = true;
      els.marketplace.appendChild(opt);
    });

    els.ebayChecklist.innerHTML = "";
    (state.me.ebay_checklist || []).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      els.ebayChecklist.appendChild(li);
    });

    els.keysStatus.textContent = state.me.has_ebay_keys
      ? "🔑 Ключи сохранены (зашифрованы). Можно оставить поля пустыми — текущие ключи сохранятся."
      : "🔑 Ключи ещё не заданы — заполните Client ID и Secret.";

    if (state.me.deletion_url && state.me.deletion_token) {
      els.deletionBox.classList.remove("hidden");
      els.deletionUrl.value = state.me.deletion_url;
      els.deletionToken.value = state.me.deletion_token;
    } else {
      els.deletionBox.classList.add("hidden");
    }

    els.ebayClientId.value = "";
    els.ebayClientSecret.value = "";
    syncEbayBlockVisibility();
  }

  function renderSearches() {
    const items = state.searches;
    els.searchesEmpty.classList.toggle("hidden", items.length > 0);
    els.searchesList.innerHTML = "";
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "card";
      const priceBits = [];
      if (item.min_price != null) priceBits.push(`от ${item.min_price}`);
      if (item.max_price != null) priceBits.push(`до ${item.max_price}`);
      const region = item.marketplace_label
        ? `<span class="badge">${escapeHtml(item.marketplace_label)}</span>`
        : "";
      card.innerHTML = `
        <h3>${escapeHtml(item.keywords)}</h3>
        <p class="meta">
          <span class="badge ${item.paused ? "pause" : ""}">${item.paused ? "пауза" : "активен"}</span>
          <span class="badge">${escapeHtml(item.source_label)}</span>
          ${region}
          ${priceBits.length ? escapeHtml(priceBits.join(" · ")) : "без фильтра цены"}
        </p>
        <div class="actions">
          <button class="chip" data-action="toggle" data-id="${item.id}" type="button">
            ${item.paused ? "▶️ Включить" : "⏸ Пауза"}
          </button>
          <button class="chip danger" data-action="delete" data-id="${item.id}" type="button">🗑 Удалить</button>
        </div>
      `;
      els.searchesList.appendChild(card);
    });
  }

  function formatLogTime(iso) {
    if (!iso) return "";
    const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function renderLogs() {
    const items = state.logs;
    els.logsEmpty.classList.toggle("hidden", items.length > 0);
    els.logsList.innerHTML = "";
    items.forEach((item) => {
      const row = document.createElement("article");
      row.className = "log-item";
      const status = item.status || "ok";
      const statusLabel = STATUS_LABELS[status] || status;
      const counts = [
        `найдено ${item.found ?? 0}`,
        `новых ${item.new_items ?? 0}`,
        `уведомл. ${item.notified ?? 0}`,
      ].join(" · ");
      row.innerHTML = `
        <div class="log-head">
          <h3>${escapeHtml(item.keywords || "—")}</h3>
          <span class="log-time">${escapeHtml(formatLogTime(item.created_at))}</span>
        </div>
        <p class="meta">
          <span class="badge status-${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>
          <span class="badge">${escapeHtml(item.source_label || item.source || "")}</span>
          ${escapeHtml(counts)}
        </p>
        ${item.message ? `<p class="log-msg">${escapeHtml(item.message)}</p>` : ""}
      `;
      els.logsList.appendChild(row);
    });
  }

  async function loadLogs() {
    const data = await api("/poll-logs");
    state.logs = data.items || [];
    renderLogs();
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function loadAll() {
    state.me = await api("/me");
    const name = state.me.username ? `@${state.me.username}` : "профиль";
    els.greeting.textContent = state.me.setup_completed ? name : "Сначала настройки";
    fillSettings();
    const list = await api("/searches");
    state.searches = list.items || [];
    renderSearches();
    await loadLogs();
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  document.querySelectorAll("[data-go]").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.go));
  });
  document.getElementById("btn-refresh").addEventListener("click", async () => {
    try {
      await loadAll();
      toast("Обновлено");
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("btn-copy-deletion").addEventListener("click", async () => {
    const text = `URL: ${els.deletionUrl.value}\nToken: ${els.deletionToken.value}`;
    try {
      await navigator.clipboard.writeText(text);
      toast("Скопировано");
    } catch {
      toast("Не удалось скопировать");
    }
  });

  document.getElementById("btn-revoke-keys").addEventListener("click", async () => {
    const ok = tg?.showConfirm
      ? await new Promise((resolve) => tg.showConfirm("Удалить eBay API ключи?", resolve))
      : window.confirm("Удалить eBay API ключи?");
    if (!ok) return;
    try {
      await api("/keys", { method: "DELETE" });
      toast("Ключи удалены");
      await loadAll();
      switchTab("settings");
    } catch (err) {
      toast(err.message);
    }
  });

  els.searchesList.addEventListener("click", async (event) => {
    const btn = event.target.closest("button[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const action = btn.dataset.action;
    try {
      if (action === "toggle") {
        const item = state.searches.find((s) => s.id === id);
        await api(`/searches/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ paused: !item.paused }),
        });
        toast(item.paused ? "Поиск включён" : "На паузе");
      }
      if (action === "delete") {
        const ok = tg?.showConfirm
          ? await new Promise((resolve) => tg.showConfirm("Удалить поиск?", resolve))
          : window.confirm("Удалить поиск?");
        if (!ok) return;
        await api(`/searches/${id}`, { method: "DELETE" });
        toast("Удалено");
      }
      await loadAll();
      switchTab("searches");
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("form-create").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      source: els.createSource.value,
      keywords: document.getElementById("create-keywords").value.trim(),
      min_price: numOrNull(document.getElementById("create-min").value),
      max_price: numOrNull(document.getElementById("create-max").value),
      buy_it_now: document.getElementById("create-bin").checked,
    };
    if (payload.source !== "poshmark" && payload.source !== "etsy") {
      payload.marketplace = els.createMarketplace.value;
    } else {
      payload.buy_it_now = false;
    }
    try {
      await api("/searches", { method: "POST", body: JSON.stringify(payload) });
      toast("Поиск создан");
      event.target.reset();
      document.getElementById("create-bin").checked = true;
      fillCreateMarketplace();
      syncCreateSourceFields();
      await loadAll();
      switchTab("searches");
      tg?.HapticFeedback?.notificationOccurred("success");
    } catch (err) {
      toast(err.message);
      tg?.HapticFeedback?.notificationOccurred("error");
    }
  });

  document.getElementById("form-settings").addEventListener("submit", async (event) => {
    event.preventDefault();
    const enabled = selectedSources();
    if (!enabled.length) {
      toast("Выберите хотя бы один источник");
      return;
    }
    const payload = {
      enabled_sources: enabled,
      ebay_marketplace: els.marketplace.value,
    };
    const clientId = els.ebayClientId.value.trim();
    const clientSecret = els.ebayClientSecret.value.trim();
    if (enabled.includes("ebay_api")) {
      if (clientId) payload.ebay_client_id = clientId;
      if (clientSecret) payload.ebay_client_secret = clientSecret;
    }
    try {
      const result = await api("/setup", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.me = result;
      fillSettings();
      toast(result.oauth_verified ? "OAuth ок, настройки сохранены" : "Настройки сохранены");
      tg?.HapticFeedback?.notificationOccurred("success");
      if (result.setup_completed) {
        const list = await api("/searches");
        state.searches = list.items || [];
        renderSearches();
        switchTab("searches");
      }
    } catch (err) {
      toast(err.message);
      tg?.HapticFeedback?.notificationOccurred("error");
    }
  });

  function numOrNull(value) {
    if (value === "" || value == null) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function initialTab() {
    const params = new URLSearchParams(location.search || "");
    const fromQuery = (params.get("tab") || "").trim();
    if (["settings", "create", "searches", "logs"].includes(fromQuery)) {
      return fromQuery;
    }
    const hash = (location.hash || "").replace("#", "");
    if (hash === "settings" || hash === "create" || hash === "searches" || hash === "logs") {
      return hash;
    }
    return null;
  }

  waitForInitData()
    .then((data) => {
      if (!data) {
        throw new Error(
          "Нет данных Telegram (initData). Откройте Mini App синей кнопкой в сообщении бота (не нижней reply-клавиатурой напрямую) или через кнопку меню у поля ввода."
        );
      }
      return loadAll();
    })
    .then(() => {
      const tab = initialTab() || (!state.me.setup_completed ? "settings" : "searches");
      switchTab(tab);
    })
    .catch((err) => showError(err.message || "Ошибка загрузки"));
})();
