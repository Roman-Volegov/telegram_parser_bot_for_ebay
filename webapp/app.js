(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#f7f5ef");
      tg.setBackgroundColor("#f7f5ef");
    } catch (_) {
      // старые клиенты могут не принимать hex
    }
  }

  const state = {
    me: null,
    searches: [],
    logs: [],
    categoryDraft: emptyCategoryDraft(),
    categoriesReturnTo: "create",
    categoriesContext: "create",
    treeTarget: null,
  };

  const STATUS_LABELS = {
    ok: "ок",
    seed: "seed",
    empty: "пусто",
    error: "ошибка",
  };

  const MAX_CATEGORIES = 10;

  const els = {
    greeting: document.getElementById("greeting"),
    searchesList: document.getElementById("searches-list"),
    searchesEmpty: document.getElementById("searches-empty"),
    logsList: document.getElementById("logs-list"),
    logsEmpty: document.getElementById("logs-empty"),
    createSources: document.getElementById("create-sources"),
    createMarketplace: document.getElementById("create-marketplace"),
    createMarketplaceWrap: document.getElementById("create-marketplace-wrap"),
    createBinWrap: document.getElementById("create-bin-wrap"),
    btnCreateCategories: document.getElementById("btn-create-categories"),
    categoriesBlocks: document.getElementById("categories-blocks"),
    sourcesBox: document.getElementById("sources-box"),
    marketplace: document.getElementById("ebay-marketplace"),
    keysStatus: document.getElementById("keys-status"),
    ebayApiBlock: document.getElementById("ebay-api-block"),
    ebayChecklist: document.getElementById("ebay-checklist"),
    ebayClientId: document.getElementById("ebay-client-id"),
    ebayClientSecret: document.getElementById("ebay-client-secret"),
    etsyApiBlock: document.getElementById("etsy-api-block"),
    etsyChecklist: document.getElementById("etsy-checklist"),
    etsyKeystring: document.getElementById("etsy-keystring"),
    etsySharedSecret: document.getElementById("etsy-shared-secret"),
    etsyKeysStatus: document.getElementById("etsy-keys-status"),
    categoriesStatus: document.getElementById("categories-status"),
    deletionBox: document.getElementById("deletion-box"),
    deletionUrl: document.getElementById("deletion-url"),
    deletionToken: document.getElementById("deletion-token"),
    toast: document.getElementById("toast"),
    errorScreen: document.getElementById("error-screen"),
    errorText: document.getElementById("error-text"),
    botUsername: document.getElementById("bot-username"),
    editDialog: document.getElementById("edit-dialog"),
    editId: document.getElementById("edit-id"),
    editSources: document.getElementById("edit-sources"),
    editKeywords: document.getElementById("edit-keywords"),
    editMin: document.getElementById("edit-min"),
    editMax: document.getElementById("edit-max"),
    editMarketplace: document.getElementById("edit-marketplace"),
    editMarketplaceWrap: document.getElementById("edit-marketplace-wrap"),
    editBin: document.getElementById("edit-bin"),
    editBinWrap: document.getElementById("edit-bin-wrap"),
    btnEditCategories: document.getElementById("btn-edit-categories"),
    treeDialog: document.getElementById("tree-dialog"),
    treeRoot: document.getElementById("tree-root"),
    treeDialogTitle: document.getElementById("tree-dialog-title"),
  };

  function emptyCategoryDraft() {
    return { ebay: [], etsy: [], poshmark: [] };
  }

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
    if (name === "settings") {
      syncEbayBlockVisibility();
      syncEtsyBlockVisibility();
      loadCategoriesStatus().catch(() => {});
    }
    if (name === "logs") loadLogs().catch((err) => toast(err.message));
    if (name === "create") {
      if (state.categoriesContext !== "create") {
        state.categoryDraft = emptyCategoryDraft();
        state.categoriesContext = "create";
      }
      syncCategoryButtons();
    }
  }

  function selectedSources() {
    return [...els.sourcesBox.querySelectorAll("input:checked")].map((el) => el.value);
  }

  function selectedSearchSources(container) {
    return [...container.querySelectorAll("input:checked")].map((el) => el.value);
  }

  function isEbaySource(source) {
    return source === "ebay_api" || source === "ebay_parser";
  }

  function categoryBlocksForSources(sources) {
    const blocks = [];
    if (sources.some(isEbaySource)) blocks.push("ebay");
    if (sources.includes("etsy")) blocks.push("etsy");
    if (sources.includes("poshmark")) blocks.push("poshmark");
    return blocks;
  }

  function draftSummary(draft, sources) {
    const bits = [];
    categoryBlocksForSources(sources).forEach((block) => {
      const count = (draft[block] || []).filter((item) => item && item.category_path).length;
      if (count) {
        const label = block === "ebay" ? "eBay" : block === "etsy" ? "Etsy" : "Poshmark";
        bits.push(`${label}: ${count}`);
      }
    });
    return bits.length ? bits.join(" · ") : "не заданы";
  }

  function syncCategoryButtons() {
    const createSources = selectedSearchSources(els.createSources);
    els.btnCreateCategories.textContent = `Категории · ${draftSummary(
      state.categoryDraft,
      createSources,
    )}`;
    els.btnCreateCategories.disabled = !createSources.length;
    const editSources = selectedSearchSources(els.editSources);
    if (els.btnEditCategories) {
      els.btnEditCategories.textContent = `Категории · ${draftSummary(
        state.categoryDraft,
        editSources,
      )}`;
      els.btnEditCategories.disabled = !editSources.length;
    }
  }

  function categoriesPayloadFromDraft(sources) {
    const payload = {};
    const blocks = categoryBlocksForSources(sources);
    if (blocks.includes("ebay")) {
      const items = (state.categoryDraft.ebay || [])
        .filter((item) => item && item.category_id)
        .map((item) => ({
          category_id: item.category_id,
          category_path: item.category_path || "",
        }));
      if (sources.includes("ebay_api")) payload.ebay_api = items;
      if (sources.includes("ebay_parser")) payload.ebay_parser = items;
    }
    if (blocks.includes("etsy") && sources.includes("etsy")) {
      payload.etsy = (state.categoryDraft.etsy || [])
        .filter((item) => item && (item.taxonomy_id != null || item.slug))
        .map((item) => ({
          taxonomy_id: item.taxonomy_id,
          slug: item.slug || null,
          category_path: item.category_path || "",
        }));
    }
    if (blocks.includes("poshmark") && sources.includes("poshmark")) {
      payload.poshmark = (state.categoryDraft.poshmark || [])
        .filter((item) => item && item.department)
        .map((item) => ({
          department: item.department,
          category: item.category || null,
          subcategory: item.subcategory || null,
          category_path: item.category_path || item.department,
        }));
    }
    return payload;
  }

  function hydrateDraftFromCategories(categories) {
    const draft = emptyCategoryDraft();
    const raw = categories || {};
    const ebayItems = raw.ebay_api || raw.ebay_parser || raw.ebay || [];
    draft.ebay = ebayItems.map((item) => ({
      category_id: item.category_id,
      category_path: item.category_path || "",
    }));
    draft.etsy = (raw.etsy || []).map((item) => ({
      taxonomy_id: item.taxonomy_id,
      slug: item.slug || null,
      category_path: item.category_path || "",
    }));
    draft.poshmark = (raw.poshmark || []).map((item) => ({
      department: item.department,
      category: item.category || null,
      subcategory: item.subcategory || null,
      category_path: item.category_path || item.department,
    }));
    return draft;
  }

  function currentMarketplace() {
    if (state.categoriesContext === "edit") {
      return els.editMarketplace.value || state.me?.ebay_marketplace || "EBAY_US";
    }
    return els.createMarketplace.value || state.me?.ebay_marketplace || "EBAY_US";
  }

  function openCategoriesPage(context) {
    state.categoriesContext = context;
    state.categoriesReturnTo = context === "edit" ? "edit" : "create";
    const sources =
      context === "edit"
        ? selectedSearchSources(els.editSources)
        : selectedSearchSources(els.createSources);
    if (!sources.length) {
      toast("Сначала выберите источники");
      return;
    }
    if (context === "edit") {
      els.editDialog.close();
    }
    renderCategoriesPage(sources);
    switchTab("categories");
  }

  function leaveCategoriesPage() {
    syncCategoryButtons();
    if (state.categoriesReturnTo === "edit") {
      switchTab("searches");
      els.editDialog.showModal();
      return;
    }
    switchTab("create");
  }

  function renderCategoriesPage(sources) {
    const blocks = categoryBlocksForSources(sources);
    const labels = {
      ebay: "eBay",
      etsy: "Etsy",
      poshmark: "Poshmark",
    };
    els.categoriesBlocks.innerHTML = "";
    blocks.forEach((block) => {
      if (!Array.isArray(state.categoryDraft[block]) || !state.categoryDraft[block].length) {
        state.categoryDraft[block] = [{}];
      }
      const section = document.createElement("section");
      section.className = "category-source-block";
      section.dataset.block = block;
      section.innerHTML = `<h3>${labels[block]}</h3><div class="category-rows"></div>`;
      const rows = section.querySelector(".category-rows");
      state.categoryDraft[block].forEach((item, index) => {
        rows.appendChild(buildCategoryRow(block, index, item));
      });
      const addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "chip";
      addBtn.textContent = "Добавить";
      addBtn.addEventListener("click", () => {
        if ((state.categoryDraft[block] || []).length >= MAX_CATEGORIES) {
          toast(`Не больше ${MAX_CATEGORIES} категорий на источник`);
          return;
        }
        state.categoryDraft[block].push({});
        renderCategoriesPage(sources);
      });
      section.appendChild(addBtn);
      els.categoriesBlocks.appendChild(section);
    });
  }

  function buildCategoryRow(block, index, item) {
    const row = document.createElement("div");
    row.className = "category-row";
    const wrap = document.createElement("div");
    wrap.className = "category-input-wrap";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Начните вводить категорию…";
    input.value = item.category_path || "";
    input.autocomplete = "off";
    const suggest = document.createElement("div");
    suggest.className = "suggest-list hidden";
    wrap.appendChild(input);
    wrap.appendChild(suggest);

    const treeBtn = document.createElement("button");
    treeBtn.type = "button";
    treeBtn.className = "tree-btn";
    treeBtn.title = "Дерево категорий";
    treeBtn.setAttribute("aria-label", "Дерево категорий");
    treeBtn.textContent = "🌳";

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "category-remove";
    removeBtn.title = "Удалить";
    removeBtn.textContent = "×";

    let timer = null;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const value = input.value.trim();
      if (!value) {
        state.categoryDraft[block][index] = {};
        suggest.classList.add("hidden");
        suggest.innerHTML = "";
        syncCategoryButtons();
        return;
      }
      // частичный ввод — ещё не выбран узел
      if (state.categoryDraft[block][index]?.category_path !== value) {
        state.categoryDraft[block][index] = {
          ...(state.categoryDraft[block][index] || {}),
          category_path: value,
        };
      }
      timer = setTimeout(async () => {
        try {
          const source = block === "ebay" ? "ebay_api" : block;
          const params = new URLSearchParams({
            source,
            q: value,
            limit: "30",
          });
          if (block === "ebay") params.set("marketplace", currentMarketplace());
          const data = await api(`/categories/search?${params.toString()}`);
          const items = data.items || [];
          suggest.innerHTML = "";
          if (!items.length) {
            suggest.classList.add("hidden");
            return;
          }
          items.forEach((node) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "suggest-item";
            btn.textContent = node.path || node.name;
            btn.addEventListener("click", () => {
              applyCategorySelection(block, index, node);
              input.value = node.path || node.name || "";
              suggest.classList.add("hidden");
              syncCategoryButtons();
            });
            suggest.appendChild(btn);
          });
          suggest.classList.remove("hidden");
        } catch (err) {
          toast(err.message);
        }
      }, 250);
    });

    input.addEventListener("blur", () => {
      setTimeout(() => suggest.classList.add("hidden"), 150);
    });

    treeBtn.addEventListener("click", () => {
      openCategoryTree(block, index);
    });

    removeBtn.addEventListener("click", () => {
      state.categoryDraft[block].splice(index, 1);
      if (!state.categoryDraft[block].length) state.categoryDraft[block] = [{}];
      const sources =
        state.categoriesContext === "edit"
          ? selectedSearchSources(els.editSources)
          : selectedSearchSources(els.createSources);
      renderCategoriesPage(sources);
      syncCategoryButtons();
    });

    row.appendChild(wrap);
    row.appendChild(treeBtn);
    row.appendChild(removeBtn);
    return row;
  }

  function applyCategorySelection(block, index, node) {
    const meta = node.meta || {};
    if (block === "ebay") {
      state.categoryDraft[block][index] = {
        category_id: meta.category_id || node.id,
        category_path: node.path || node.name,
      };
      return;
    }
    if (block === "etsy") {
      const taxonomyId =
        meta.taxonomy_id != null
          ? meta.taxonomy_id
          : Number.isFinite(Number(node.id))
            ? Number(node.id)
            : null;
      const slug =
        meta.slug ||
        (String(node.id || "").startsWith("slug:")
          ? String(node.id).slice(5)
          : null);
      state.categoryDraft[block][index] = {
        taxonomy_id: taxonomyId,
        slug,
        category_path: node.path || node.name,
      };
      return;
    }
    state.categoryDraft[block][index] = {
      department: meta.department,
      category: meta.category || null,
      subcategory: meta.subcategory || null,
      category_path: node.path || node.name,
    };
  }

  async function openCategoryTree(block, index) {
    state.treeTarget = { block, index };
    els.treeDialogTitle.textContent =
      block === "ebay" ? "Дерево eBay" : block === "etsy" ? "Дерево Etsy" : "Дерево Poshmark";
    els.treeRoot.innerHTML = "<p class='hint'>Загрузка…</p>";
    els.treeDialog.showModal();
    try {
      await renderTreeLevel(els.treeRoot, block, null);
    } catch (err) {
      els.treeRoot.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
    }
  }

  async function renderTreeLevel(container, block, parentId) {
    const source = block === "ebay" ? "ebay_api" : block;
    const params = new URLSearchParams({ source });
    if (parentId) params.set("parent_id", parentId);
    if (block === "ebay") params.set("marketplace", currentMarketplace());
    const data = await api(`/categories?${params.toString()}`);
    const items = data.items || [];
    container.innerHTML = "";
    if (!items.length) {
      container.innerHTML = "<p class='hint'>Нет вложенных категорий</p>";
      return;
    }
    items.forEach((node) => {
      const wrap = document.createElement("div");
      wrap.className = "tree-node";
      const row = document.createElement("div");
      row.className = "tree-node-row";
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "tree-toggle";
      toggle.textContent = node.has_children ? "▸" : "·";
      toggle.disabled = !node.has_children;
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "tree-pick";
      pick.textContent = node.name;
      const children = document.createElement("div");
      children.className = "tree-children hidden";
      pick.addEventListener("click", () => {
        if (!state.treeTarget) return;
        const { block: targetBlock, index } = state.treeTarget;
        applyCategorySelection(targetBlock, index, node);
        els.treeDialog.close();
        const sources =
          state.categoriesContext === "edit"
            ? selectedSearchSources(els.editSources)
            : selectedSearchSources(els.createSources);
        renderCategoriesPage(sources);
        syncCategoryButtons();
      });
      let opened = false;
      toggle.addEventListener("click", async () => {
        if (!node.has_children) return;
        opened = !opened;
        toggle.textContent = opened ? "▾" : "▸";
        children.classList.toggle("hidden", !opened);
        if (opened && !children.dataset.loaded) {
          children.textContent = "…";
          try {
            await renderTreeLevel(children, block, node.id);
            children.dataset.loaded = "1";
          } catch (err) {
            children.textContent = err.message;
          }
        }
      });
      row.appendChild(toggle);
      row.appendChild(pick);
      wrap.appendChild(row);
      wrap.appendChild(children);
      container.appendChild(wrap);
    });
  }

  async function loadCategoriesStatus() {
    if (!els.categoriesStatus) return;
    try {
      const status = await api("/categories/status");
      const trees = status.trees || [];
      const bits = trees.map((tree) => {
        const label = tree.marketplace || tree.source;
        const when = tree.updated_at ? String(tree.updated_at).slice(0, 10) : "—";
        return `${label}: ${tree.nodes} (${when})`;
      });
      els.categoriesStatus.textContent = bits.length
        ? bits.slice(0, 4).join(" · ") + (bits.length > 4 ? "…" : "")
        : "Каталоги ещё не загружены";
    } catch (err) {
      els.categoriesStatus.textContent = err.message;
    }
  }

  function syncEbayBlockVisibility() {
    const show = selectedSources().includes("ebay_api");
    els.ebayApiBlock.classList.toggle("hidden", !show);
  }

  function syncEtsyBlockVisibility() {
    const show = selectedSources().includes("etsy");
    els.etsyApiBlock.classList.toggle("hidden", !show);
  }

  function syncCreateSourceFields() {
    const sources = selectedSearchSources(els.createSources);
    const hasEbay = sources.some((source) => source !== "poshmark" && source !== "etsy");
    els.createBinWrap.classList.toggle("hidden", !hasEbay);
    els.createMarketplaceWrap.classList.toggle("hidden", !hasEbay);
    syncCategoryButtons();
  }

  function syncEditSourceFields() {
    const sources = selectedSearchSources(els.editSources);
    const hasEbay = sources.some((source) => source !== "poshmark" && source !== "etsy");
    els.editBinWrap.classList.toggle("hidden", !hasEbay);
    els.editMarketplaceWrap.classList.toggle("hidden", !hasEbay);
    syncCategoryButtons();
  }

  function fillSearchSourcePicker(container, selected = []) {
    const labels = state.me.source_labels || {};
    const enabled = state.me.enabled_sources || [];
    const checked = new Set(selected);
    container.innerHTML = "";
    enabled.forEach((source, index) => {
      const row = document.createElement("label");
      row.className = "source-item";
      row.innerHTML = `
        <input type="checkbox" value="${source}" ${
          checked.has(source) || (!selected.length && index === 0) ? "checked" : ""
        } />
        <span>${escapeHtml(labels[source] || source)}</span>
      `;
      container.appendChild(row);
    });
  }

  function fillCreateMarketplace() {
    fillMarketplaceSelect(
      els.createMarketplace,
      state.me.ebay_marketplace || "EBAY_US",
    );
  }

  function fillMarketplaceSelect(select, selected) {
    const labels = state.me.ebay_marketplace_labels || {};
    const markets = state.me.ebay_marketplaces || ["EBAY_US"];
    select.innerHTML = "";
    markets.forEach((market) => {
      const opt = document.createElement("option");
      opt.value = market;
      opt.textContent = labels[market] || market;
      if (market === selected) opt.selected = true;
      select.appendChild(opt);
    });
  }

  function fillSettings() {
    const labels = state.me.source_labels || {};
    const enabled = new Set(state.me.enabled_sources || []);
    const marketLabels = state.me.ebay_marketplace_labels || {};

    fillSearchSourcePicker(els.createSources);
    els.createSources.onchange = syncCreateSourceFields;
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
      input.addEventListener("change", () => {
        syncEbayBlockVisibility();
        syncEtsyBlockVisibility();
      });
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

    els.etsyChecklist.innerHTML = "";
    (state.me.etsy_checklist || []).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      els.etsyChecklist.appendChild(li);
    });

    els.keysStatus.textContent = state.me.has_ebay_keys
      ? "Ключи сохранены (зашифрованы). Можно оставить поля пустыми — текущие ключи сохранятся."
      : "Ключи ещё не заданы — заполните Client ID и Secret.";

    els.etsyKeysStatus.textContent = state.me.has_etsy_keys
      ? "Open API ключ сохранён (зашифрован). Пустые поля — оставить текущий."
      : "Ключ не задан — используется Playwright.";

    if (state.me.deletion_url && state.me.deletion_token) {
      els.deletionBox.classList.remove("hidden");
      els.deletionUrl.value = state.me.deletion_url;
      els.deletionToken.value = state.me.deletion_token;
    } else {
      els.deletionBox.classList.add("hidden");
    }

    syncEbayBlockVisibility();
    syncEtsyBlockVisibility();
    els.ebayClientId.value = "";
    els.ebayClientSecret.value = "";
    els.etsyKeystring.value = "";
    els.etsySharedSecret.value = "";
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
      const sourceBadges = (item.source_labels || [item.source_label])
        .map((label) => `<span class="badge">${escapeHtml(label)}</span>`)
        .join("");
      card.innerHTML = `
        <h3>${escapeHtml(item.keywords)}</h3>
        <p class="meta">
          <span class="badge ${item.paused ? "pause" : ""}">${item.paused ? "пауза" : "активен"}</span>
          ${sourceBadges}
          ${region}
          ${priceBits.length ? escapeHtml(priceBits.join(" · ")) : "без фильтра цены"}
        </p>
        <div class="actions">
          <button class="chip" data-action="edit" data-id="${item.id}" type="button">✏️ Изменить</button>
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
    if (state.me.bot_username) {
      els.botUsername.textContent = `@${state.me.bot_username}`;
    }
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
      toast("eBay ключи удалены");
      await loadAll();
      switchTab("settings");
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("btn-revoke-etsy-keys").addEventListener("click", async () => {
    const ok = tg?.showConfirm
      ? await new Promise((resolve) => tg.showConfirm("Удалить Etsy API ключ?", resolve))
      : window.confirm("Удалить Etsy API ключ?");
    if (!ok) return;
    try {
      await api("/keys/etsy", { method: "DELETE" });
      toast("Etsy ключ удалён");
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
      if (action === "edit") {
        const item = state.searches.find((search) => search.id === id);
        els.editId.value = String(item.id);
        fillSearchSourcePicker(els.editSources, item.sources || [item.source]);
        els.editSources.onchange = syncEditSourceFields;
        fillMarketplaceSelect(
          els.editMarketplace,
          item.marketplace || state.me.ebay_marketplace || "EBAY_US",
        );
        els.editBin.checked = Boolean(item.buy_it_now);
        state.categoryDraft = hydrateDraftFromCategories(item.categories || {});
        syncEditSourceFields();
        els.editKeywords.value = item.keywords;
        els.editMin.value = item.min_price ?? "";
        els.editMax.value = item.max_price ?? "";
        els.editDialog.showModal();
        return;
      }
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

  document.getElementById("btn-edit-cancel").addEventListener("click", () => {
    els.editDialog.close();
  });

  els.btnCreateCategories.addEventListener("click", () => {
    openCategoriesPage("create");
  });
  els.btnEditCategories.addEventListener("click", () => {
    openCategoriesPage("edit");
  });
  document.getElementById("btn-categories-back").addEventListener("click", leaveCategoriesPage);
  document.getElementById("btn-categories-done").addEventListener("click", leaveCategoriesPage);
  document.getElementById("btn-tree-close").addEventListener("click", () => {
    els.treeDialog.close();
  });
  document.getElementById("btn-refresh-categories").addEventListener("click", async () => {
    const btn = document.getElementById("btn-refresh-categories");
    btn.disabled = true;
    try {
      toast("Обновление каталогов…");
      const result = await api("/categories/refresh", { method: "POST", body: "{}" });
      await loadCategoriesStatus();
      toast(result.message || (result.ok ? "Каталоги обновлены" : "Частичное обновление"));
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("form-edit").addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = Number(els.editId.value);
    const minValue = els.editMin.value;
    const maxValue = els.editMax.value;
    const sources = selectedSearchSources(els.editSources);
    if (!sources.length) {
      toast("Выберите хотя бы один источник");
      return;
    }
    const payload = {
      sources,
      keywords: els.editKeywords.value.trim(),
      min_price: numOrNull(minValue),
      max_price: numOrNull(maxValue),
      clear_min_price: minValue === "",
      clear_max_price: maxValue === "",
    };
    if (sources.some((source) => source !== "poshmark" && source !== "etsy")) {
      payload.marketplace = els.editMarketplace.value;
      payload.buy_it_now = els.editBin.checked;
    }
    payload.categories = categoriesPayloadFromDraft(sources);
    try {
      await api(`/searches/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      els.editDialog.close();
      state.categoryDraft = emptyCategoryDraft();
      await loadAll();
      switchTab("searches");
      toast("Поиск обновлён");
    } catch (err) {
      if (tg?.showAlert) {
        await new Promise((resolve) => tg.showAlert(err.message || "Ошибка", resolve));
      } else {
        toast(err.message);
      }
    }
  });

  document.getElementById("form-create").addEventListener("submit", async (event) => {
    event.preventDefault();
    const sources = selectedSearchSources(els.createSources);
    if (!sources.length) {
      toast("Выберите хотя бы один источник");
      return;
    }
    const payload = {
      sources,
      keywords: document.getElementById("create-keywords").value.trim(),
      min_price: numOrNull(document.getElementById("create-min").value),
      max_price: numOrNull(document.getElementById("create-max").value),
      buy_it_now: document.getElementById("create-bin").checked,
    };
    if (payload.sources.some((source) => source !== "poshmark" && source !== "etsy")) {
      payload.marketplace = els.createMarketplace.value;
    } else {
      payload.buy_it_now = false;
    }
    payload.categories = categoriesPayloadFromDraft(sources);
    try {
      const result = await api("/searches", { method: "POST", body: JSON.stringify(payload) });
      const msg = result.message || "Новый поиск создан";
      if (tg?.showAlert) {
        await new Promise((resolve) => tg.showAlert(msg, resolve));
      } else {
        toast(msg);
      }
      event.target.reset();
      document.getElementById("create-bin").checked = true;
      state.categoryDraft = emptyCategoryDraft();
      fillSearchSourcePicker(els.createSources);
      fillCreateMarketplace();
      syncCreateSourceFields();
      await loadAll();
      switchTab("searches");
      tg?.HapticFeedback?.notificationOccurred("success");
    } catch (err) {
      if (tg?.showAlert) {
        await new Promise((resolve) => tg.showAlert(err.message || "Ошибка", resolve));
      } else {
        toast(err.message);
      }
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
    const etsyKey = els.etsyKeystring.value.trim();
    const etsySecret = els.etsySharedSecret.value.trim();
    if (enabled.includes("etsy")) {
      if (etsyKey) payload.etsy_keystring = etsyKey;
      if (etsySecret) payload.etsy_shared_secret = etsySecret;
    }
    try {
      const result = await api("/setup", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.me = result;
      fillSettings();
      const bits = [];
      if (result.oauth_verified) bits.push("eBay OAuth ок");
      if (result.etsy_verified) bits.push("Etsy ключ ок");
      toast(bits.length ? `${bits.join(", ")}, настройки сохранены` : "Настройки сохранены");
      tg?.HapticFeedback?.notificationOccurred("success");
      if (result.setup_completed) {
        const list = await api("/searches");
        state.searches = list.items || [];
        renderSearches();
        switchTab("searches");
      }
    } catch (err) {
      if (tg?.showAlert) {
        await new Promise((resolve) => tg.showAlert(err.message || "Ошибка", resolve));
      } else {
        toast(err.message);
      }
      tg?.HapticFeedback?.notificationOccurred("error");
    }
  });

  function numOrNull(value) {
    if (value === "" || value == null) return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
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
      switchTab("searches");
    })
    .catch((err) => showError(err.message || "Ошибка загрузки"));
})();
