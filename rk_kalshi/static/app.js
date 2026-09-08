(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    fills: $("metric-fills"),
    pnl: $("metric-pnl"),
    realized: $("metric-realized"),
    size: $("metric-size"),
    kill: $("metric-kill"),
    hintFills: $("hint-fills"),
    hintCash: $("hint-cash"),
    hintSize: $("hint-size"),
    hintKill: $("hint-kill"),
    sizePill: $("size-pill"),
    killPill: $("kill-pill"),
    runState: $("run-state"),
    cycles: $("cycles"),
    sleep: $("sleep"),
    btnOnce: $("btn-once"),
    btnCycles: $("btn-cycles"),
    startPanel: $("start-panel"),
    startForm: $("start-form"),
    startingCash: $("starting-cash"),
    maxPerTrade: $("max-per-trade"),
    dailyLoss: $("daily-loss"),
    startSleep: $("start-sleep"),
    btnStart: $("btn-start"),
    btnStop: $("btn-stop"),
    btnClearSession: $("btn-clear-session"),
    startHint: $("start-hint"),
    categorySelect: $("category-select"),
    matchSelect: $("match-select"),
    matchHint: $("match-hint"),
    tradeStyle: $("trade-style"),
    tradeStyleHint: $("trade-style-hint"),
    contractUrl: $("contract-url"),
    btnUseContract: $("btn-use-contract"),
    btnClearContract: $("btn-clear-contract"),
    contractStatus: $("contract-status"),
    liveMatchesOnly: $("live-matches-only"),
    signalMode: $("signal-mode"),
    filterBitcoinMarkets: $("filter-bitcoin-markets"),
    filterLiveMarkets: $("filter-live-markets"),
    log: $("log"),
    logCount: $("log-count"),
    btnClearLogs: $("btn-clear-logs"),
    marketsBody: $("markets-body"),
    marketsMeta: $("markets-meta"),
    autoMarkets: $("auto-markets"),
    btnMarkets: $("btn-markets"),
    fillsBody: $("fills-body"),
    fillsMeta: $("fills-meta"),
    footer: $("footer-meta"),
    accountBanner: $("account-banner"),
    accountPill: $("account-pill"),
    accountForm: $("account-form"),
    accountConn: $("account-conn"),
    accountHint: $("account-hint"),
    btnConnect: $("btn-connect"),
    btnDisconnect: $("btn-disconnect"),
    enableLiveTrading: $("enable-live-trading"),
    confirmLiveMoney: $("confirm-live-money"),
    liveWarning: $("live-warning"),
    modeBanner: $("mode-banner"),
    viewChip: $("view-chip"),
    autoAccount: $("auto-account"),
    btnAccountRefresh: $("btn-account-refresh"),
    accountMeta: $("account-meta"),
    liveBalance: $("live-balance"),
    livePortfolio: $("live-portfolio"),
    livePositions: $("live-positions"),
    liveFills: $("live-fills"),
    livePosHint: $("live-pos-hint"),
    livePositionsBody: $("live-positions-body"),
    liveFillsBody: $("live-fills-body"),
    liveOrdersBody: $("live-orders-body"),
    deskTitle: $("desk-title"),
    btnDeskPaper: $("btn-desk-paper"),
    btnDeskLive: $("btn-desk-live"),
    startEyebrow: $("start-eyebrow"),
    startHeading: $("start-heading"),
    startLede: $("start-lede"),
    bankrollLabel: $("bankroll-label"),
    signalModeLabel: $("signal-mode-label"),
    contractHeading: $("contract-heading"),
    contractCopy: $("contract-copy"),
    btnCancelOpen: $("btn-cancel-open"),
    deskGate: $("desk-gate"),
    deskGateConnect: $("desk-gate-connect"),
    deskGateConfirm: $("desk-gate-confirm"),
    deskGateBalance: $("desk-gate-balance"),
    deskGateError: $("desk-gate-error"),
    btnGateConnect: $("btn-gate-connect"),
    btnGateEnter: $("btn-gate-enter"),
    btnGateCancel: $("btn-gate-cancel"),
    gateEnableLive: $("gate-enable-live"),
    gateConfirmLive: $("gate-confirm-live"),
  };

  const PAPER_LEDE = document.getElementById("start-lede")
    ? document.getElementById("start-lede").innerHTML
    : "";
  const LIVE_LEDE =
    "Same controls as Paper — category, live match, Safe · Conservative · Active · Aggressive, "
    + "and as_obi / hybrid / llm research — but this desk spends <strong>real Kalshi cash</strong>. "
    + "Bankroll is your connected balance; max $/trade sizes the order (not 1–2 contracts) and daily loss is hard-capped. "
    + "After a YES fill, the bot can sell YES (buy NO) when that round-trip locks a profit after fees. "
    + "Hybrid / ChatGPT may inform new entries; <strong>pair-lock covers and risk gates always win</strong>. "
    + "<strong>Not financial advice</strong>. There is <strong>no guaranteed profitable model</strong>. "
    + "<code>can_size_up</code> stays locked. Stop ends polling and cancels open orders from this desk. "
    + "<strong>Clear view</strong> wipes this screen’s log only — not cancel-all. "
    + "Use <strong>Cancel open Kalshi orders</strong> if you intend to cancel resting orders.";

  let deskMode = "paper";
  let lastLiveBalance = null;
  let lastLiveCaps = null;
  let running = false;
  let marketsTimer = null;
  let accountTimer = null;
  let lastMarkets = null;
  let lastTarget = { active: false, event_ticker: "", market_ticker: "", label: "", asset_class: "" };
  let contractError = "";
  let lastCatalog = { categories: [], events: [] };
  let stylePresets = { default: "active", styles: [] };
  const pollMs = 700;

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!response.ok) {
      const detail = data && data.detail ? data.detail : response.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function parseTimestamp(value) {
    if (value == null || value === "") return null;
    const text = String(value).trim();
    for (const candidate of [text, text.replace(" ", "T")]) {
      const parsed = new Date(candidate);
      if (!Number.isNaN(parsed.getTime())) return parsed;
    }
    return null;
  }

  function formatLocalDateTime(value) {
    const parsed = parseTimestamp(value);
    if (!parsed) return value == null ? "" : String(value);
    return parsed.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    });
  }

  function localTimeZoneLabel() {
    try {
      const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
        .formatToParts(new Date());
      const short = (parts.find((part) => part.type === "timeZoneName") || {}).value;
      return short || Intl.DateTimeFormat().resolvedOptions().timeZone || "local";
    } catch {
      return "local";
    }
  }

  function applyLocalTimeHeaders() {
    const head = $("fills-time-head");
    if (head) head.textContent = `local time (${localTimeZoneLabel()})`;
  }

  function formatLogLine(line) {
    const match = String(line).match(/^(\S+)\s{2}([\s\S]*)$/);
    if (!match) return line;
    return `${formatLocalDateTime(match[1])}  ${match[2]}`;
  }

  function fmt(value, digits = 4) {
    const num = Number(value);
    if (!Number.isFinite(num)) return "—";
    return num.toFixed(digits);
  }

  function signedClass(value) {
    const num = Number(value);
    if (num > 0) return "metric pos";
    if (num < 0) return "metric neg";
    return "metric";
  }

  function setRunEnabled(enabled) {
    els.btnOnce.disabled = !enabled;
    els.btnCycles.disabled = !enabled;
    els.btnStart.disabled = !enabled;
    els.btnStart.hidden = !enabled;
    els.btnStop.hidden = enabled;
    els.startingCash.disabled = !enabled || deskMode === "live";
    els.maxPerTrade.disabled = !enabled;
    els.dailyLoss.disabled = !enabled;
    els.startSleep.disabled = !enabled;
    if (els.liveMatchesOnly) els.liveMatchesOnly.disabled = !enabled;
    if (els.signalMode) els.signalMode.disabled = !enabled;
    if (els.tradeStyle) els.tradeStyle.disabled = !enabled;
    if (els.categorySelect) els.categorySelect.disabled = !enabled;
    if (els.matchSelect) els.matchSelect.disabled = !enabled;
    if (els.contractUrl) els.contractUrl.disabled = !enabled;
    if (els.btnUseContract) els.btnUseContract.disabled = !enabled;
    if (els.btnClearContract) els.btnClearContract.disabled = !enabled;
    if (els.btnClearSession) {
      els.btnClearSession.hidden = !enabled;
      els.btnClearSession.disabled = !enabled;
    }
    if (els.enableLiveTrading) els.enableLiveTrading.disabled = !enabled || els.enableLiveTrading.dataset.locked === "1";
    if (els.confirmLiveMoney) {
      const liveOn = Boolean(els.enableLiveTrading && els.enableLiveTrading.checked);
      els.confirmLiveMoney.disabled = !enabled || !liveOn;
    }
    els.startPanel.classList.toggle("is-running", !enabled);
    paintStartButton();
  }

  function renderLog(lines) {
    if (!lines || !lines.length) {
      els.log.textContent = running
        ? (deskMode === "live" ? "LIVE session started…" : "Paper-run started…")
        : (deskMode === "live" ? "Waiting for a LIVE session…" : "Waiting for a paper-run…");
      els.logCount.textContent = "0 lines";
      return;
    }
    els.log.textContent = lines.map(formatLogLine).join("\n");
    els.log.scrollTop = els.log.scrollHeight;
    els.logCount.textContent = `${lines.length} line${lines.length === 1 ? "" : "s"}`;
  }

  function renderMarkets(payload) {
    const all = payload.markets || [];
    const liveCount = all.filter((m) => m.in_play).length;
    const btcCount = all.filter((m) => m.asset_class === "bitcoin").length;
    const liveOnly = Boolean(els.filterLiveMarkets && els.filterLiveMarkets.checked);
    const showBitcoin = !els.filterBitcoinMarkets || els.filterBitcoinMarkets.checked;
    const rows = all.filter((m) => {
      if (lastTarget.active) {
        const eventKey = String(lastTarget.event_ticker || "").toUpperCase();
        const marketKey = String(lastTarget.market_ticker || "").toUpperCase();
        const matches = marketKey
          ? String(m.ticker || "").toUpperCase() === marketKey
          : String(m.event_ticker || m.match_id || "").toUpperCase() === eventKey;
        if (m.asset_class === "bitcoin") return showBitcoin && matches;
        return matches;
      }
      if (m.asset_class === "bitcoin") return showBitcoin;
      if (liveOnly) return Boolean(m.in_play);
      return true;
    });
    els.marketsMeta.textContent =
      `${btcCount} btc · ${liveCount} live tennis / ${payload.count} open · ${payload.series.join(", ")} · ${fmt(payload.latency_ms, 1)} ms`;
    if (!all.length) {
      els.marketsBody.innerHTML =
        '<tr><td colspan="13" class="empty">No open markets (empty series filter).</td></tr>';
      return;
    }
    if (!rows.length) {
      els.marketsBody.innerHTML =
        '<tr><td colspan="13" class="empty">No rows for the current Bitcoin / live-tennis filters.</td></tr>';
      return;
    }
    els.marketsBody.innerHTML = rows.map((m) => `
      <tr>
        <td>${m.in_play ? '<span class="live-dot">LIVE</span>' : "—"}</td>
        <td>${m.asset_class === "bitcoin" ? '<span class="btc-dot">BTC</span>' : escapeHtml(m.asset_class || "tennis")}</td>
        <td class="ticker">${escapeHtml(m.ticker)}</td>
        <td>${escapeHtml(m.event_name || "")}</td>
        <td class="num">${fmt(m.yes_bid, 3)}</td>
        <td class="num">${fmt(m.yes_ask, 3)}</td>
        <td class="num">${m.yes_mid == null ? "—" : fmt(m.yes_mid, 3)}</td>
        <td class="num">${fmt(m.last_price, 3)}</td>
        <td class="num">${m.spread_cents == null ? "—" : fmt(m.spread_cents, 1)}</td>
        <td class="num">${m.order_book_imbalance == null ? "—" : fmt(m.order_book_imbalance, 2)}</td>
        <td class="num">${fmt(m.yes_bid_size, 0)}</td>
        <td class="num">${fmt(m.yes_ask_size, 0)}</td>
        <td class="num">${fmt(m.volume, 1)}</td>
      </tr>
    `).join("");
  }

  function renderFills(payload) {
    const rows = payload.fills || [];
    els.fillsMeta.textContent =
      `${rows.length} fill${rows.length === 1 ? "" : "s"} · local clock · newest first`;
    if (!rows.length) {
      els.fillsBody.innerHTML =
        '<tr><td colspan="11" class="empty">No paper fills yet — run a cycle.</td></tr>';
      return;
    }
    els.fillsBody.innerHTML = rows.map((f) => `
      <tr>
        <td class="when">${escapeHtml(formatLocalDateTime(f.timestamp))}</td>
        <td class="ticker">${escapeHtml(f.ticker || "")}</td>
        <td>${escapeHtml(f.side || "")}</td>
        <td class="num">${fmt(f.fill_price)}</td>
        <td class="num">${fmt(f.live_mid)}</td>
        <td class="num">${fmt(f.edge_cents, 2)}</td>
        <td class="num">${fmt(f.running_pnl)}</td>
        <td>${escapeHtml(f.event_name || "")}</td>
        <td class="ticker">${escapeHtml(f.match_id || "")}</td>
        <td>${f.can_size_up ? "true" : "false"}</td>
        <td>${escapeHtml(f.edge_thesis || "")}</td>
      </tr>
    `).join("");
  }

  function renderSummary(pnl, status) {
    const fills = pnl.fills ?? 0;
    els.fills.textContent = String(fills);
    els.pnl.textContent = fmt(pnl.running_pnl);
    els.pnl.className = signedClass(pnl.running_pnl);
    els.realized.textContent = fmt(pnl.realized_delta_sum);
    els.realized.className = signedClass(pnl.realized_delta_sum);
    els.size.textContent = pnl.can_size_up ? "true" : "false";
    els.size.className = pnl.can_size_up ? "metric pos" : "metric lock-text";

    const killed = Boolean(pnl.killed);
    els.kill.textContent = killed ? "HIT" : "off";
    els.kill.className = killed ? "metric neg" : "metric";
    els.hintFills.textContent = `state fills=${pnl.fill_count_state ?? fills}`;
    const cash = pnl.cash == null ? "—" : fmt(pnl.cash);
    els.hintCash.textContent = `cash ${cash} · start ${fmt(pnl.starting_cash)}`;
    els.hintSize.textContent =
      `allow_size_up=${status.allow_size_up} · min ${status.min_fills_before_size_up} fills`;
    els.hintKill.textContent = killed
      ? (pnl.kill_reason || "daily loss kill-switch")
      : `daily loss limit $${fmt(status.daily_loss_limit, 0)}`;

    els.sizePill.textContent = pnl.can_size_up ? "can_size_up unlocked" : "can_size_up locked";
    els.sizePill.className = pnl.can_size_up ? "pill ok" : "pill lock";
    els.killPill.textContent = killed ? "kill-switch HIT" : "kill-switch clear";
    els.killPill.className = killed ? "pill hot" : "pill ok";

    const series = (status.series_tickers || []).join(", ");
    const account = status.account || {};
    const algo = status.signal_algorithm || "Avellaneda–Stoikov + OBI";
    const liveOn = deskMode === "live";
    if (status.live_caps) lastLiveCaps = status.live_caps;
    els.footer.textContent =
      `localhost · desk=${deskMode} · paper_mode=${liveOn ? "false" : "true"} · live.enabled=${Boolean(status.live_enabled)} · ${algo} · account=${account.status || "disconnected"} · btc=${status.trade_bitcoin} tennis=${status.trade_tennis} · live_matches_only=${status.live_matches_only} · series ${series} · edge ${status.edge_threshold_cents}¢`;
    const signalNote = $("signal-note");
    if (signalNote && status.signal_algorithm) {
      signalNote.textContent = `${status.signal_algorithm} · ${liveOn ? "LIVE real money" : "paper only"} · not a predictor`;
    }
    renderAccountStatus(account, status.account_environment_default, status);
    paintDeskChrome(status);
    if (!els.startForm.dataset.seeded) {
      if (status.starting_cash != null) els.startingCash.value = status.starting_cash;
      if (status.max_dollars_per_ticker != null) els.maxPerTrade.value = status.max_dollars_per_ticker;
      if (status.daily_loss_limit != null) els.dailyLoss.value = status.daily_loss_limit;
      if (status.cycle_sleep_s != null) {
        els.startSleep.value = status.cycle_sleep_s;
        els.sleep.value = status.cycle_sleep_s;
      }
      if (els.liveMatchesOnly && status.live_matches_only != null) {
        els.liveMatchesOnly.checked = Boolean(status.live_matches_only);
      }
      if (els.signalMode && status.signal_mode) {
        els.signalMode.value = status.signal_mode;
      }
      if (els.tradeStyle && status.trade_style) {
        els.tradeStyle.value = status.trade_style;
      }
      if (els.categorySelect && status.target_category_id) {
        els.categorySelect.value = status.target_category_id;
      }
      paintTradeStyleHint(status.trade_style_blurb);
      els.startForm.dataset.seeded = "1";
      els.sleep.dataset.seeded = "1";
    }
    renderTarget(status.target || {});
    const books = [
      lastTarget.active
        ? `${lastTarget.asset_class || "kalshi"} ${lastTarget.label || lastTarget.event_ticker}`
        : categoryBookLabel(status),
    ].filter(Boolean).join(" · ") || "no books selected";
    els.startHint.textContent = running
      ? `${liveOn ? "LIVE" : "Paper"} session running (${books} · ${status.trade_style || "active"}) — Stop ends polling.`
        + (liveOn
          ? " Clear view is local-only and does not cancel Kalshi orders."
          : " Clear wipes the local paper session, not a live Kalshi account.")
      : `Mode ${status.trade_style || "active"} · ${liveOn ? "LIVE real money" : "paper"} · max $${fmt(status.max_dollars_per_ticker, 0)}/trade · daily loss $${fmt(status.daily_loss_limit, 0)} · ${books}`;
  }

  function renderTarget(target) {
    lastTarget = {
      active: Boolean(target && target.active),
      event_ticker: (target && target.event_ticker) || "",
      market_ticker: (target && target.market_ticker) || "",
      label: (target && target.label) || "",
      url: (target && target.url) || "",
      asset_class: (target && target.asset_class) || "",
    };
    if (target && target.error) {
      contractError = String(target.error);
    } else if (target && target.active) {
      contractError = "";
    }
    paintContractStatus();
    if (els.matchSelect && lastTarget.event_ticker) {
      const exists = Array.from(els.matchSelect.options).some((opt) => opt.value === lastTarget.event_ticker);
      if (!exists) {
        const opt = document.createElement("option");
        opt.value = lastTarget.event_ticker;
        opt.textContent = lastTarget.label || lastTarget.event_ticker;
        els.matchSelect.appendChild(opt);
      }
      els.matchSelect.value = lastTarget.event_ticker;
    } else if (els.matchSelect && !lastTarget.active) {
      els.matchSelect.value = "";
    }
    if (els.contractUrl && lastTarget.url && !els.contractUrl.value) {
      els.contractUrl.value = lastTarget.url;
    }
    if (lastMarkets) renderMarkets(lastMarkets);
  }

  function paintContractStatus() {
    if (!els.contractStatus) return;
    if (contractError) {
      els.contractStatus.textContent = contractError;
      els.contractStatus.classList.add("error");
      return;
    }
    els.contractStatus.classList.remove("error");
    const categoryLabel = selectedCategoryLabel();
    if (!lastTarget.active) {
      els.contractStatus.textContent =
        `No match selected — ${deskMode === "live" ? "LIVE" : "paper"} can scan every live book in ${categoryLabel}.`;
    } else if (lastTarget.market_ticker) {
      const kind = lastTarget.asset_class || "Kalshi";
      els.contractStatus.textContent =
        `Selected ${kind} contract ${lastTarget.market_ticker} on ${lastTarget.event_ticker}. ${deskMode === "live" ? "LIVE" : "Paper"} trading will use only this market.`;
    } else {
      const kind = lastTarget.asset_class || "Kalshi";
      els.contractStatus.textContent =
        `Selected ${kind} event ${lastTarget.label || lastTarget.event_ticker}. ${deskMode === "live" ? "LIVE" : "Paper"} trading will use only this event’s contracts.`;
    }
  }

  function selectedCategoryLabel() {
    const id = (els.categorySelect && els.categorySelect.value) || "all";
    const hit = (lastCatalog.categories || []).find((row) => row.id === id);
    return (hit && hit.label) || "this category";
  }

  function categoryKind(id) {
    const hit = (lastCatalog.categories || []).find((row) => row.id === id);
    return (hit && hit.kind) || (id === "all" ? "all" : "");
  }

  function categoryBookLabel(status) {
    const id = (status && status.target_category_id) || (els.categorySelect && els.categorySelect.value) || "all";
    const hit = ((status && status.categories) || lastCatalog.categories || []).find((row) => row.id === id);
    if (hit) return hit.label;
    if (status && status.trade_bitcoin && status.trade_tennis) return "all live books";
    if (status && status.trade_bitcoin) return "Bitcoin";
    if (status && status.trade_tennis) return status.live_matches_only ? "live tennis" : "tennis";
    return "no books selected";
  }

  function paintTradeStyleHint(blurb) {
    if (!els.tradeStyleHint) return;
    if (blurb) {
      els.tradeStyleHint.textContent = blurb;
      return;
    }
    const id = (els.tradeStyle && els.tradeStyle.value) || "active";
    const hit = (stylePresets.styles || []).find((row) => row.id === id);
    if (hit && hit.blurb) els.tradeStyleHint.textContent = hit.blurb;
  }

  function applyTradeStyleDefaults() {
    const id = (els.tradeStyle && els.tradeStyle.value) || "active";
    const hit = (stylePresets.styles || []).find((row) => row.id === id);
    if (!hit) return;
    if (els.maxPerTrade) els.maxPerTrade.value = hit.max_dollars_per_ticker;
    if (els.dailyLoss) els.dailyLoss.value = hit.daily_loss_limit;
    if (els.startSleep) els.startSleep.value = hit.cycle_sleep_s;
    if (els.sleep) els.sleep.value = hit.cycle_sleep_s;
    paintTradeStyleHint(hit.blurb);
  }

  function populateCategorySelect(payload) {
    if (!els.categorySelect) return;
    const current = els.categorySelect.value || "all";
    const categories = payload.categories || [];
    if (!categories.length) return;
    lastCatalog.categories = categories;
    els.categorySelect.innerHTML = "";
    for (const row of categories) {
      const opt = document.createElement("option");
      opt.value = row.id;
      opt.textContent = row.label;
      els.categorySelect.appendChild(opt);
    }
    if (Array.from(els.categorySelect.options).some((opt) => opt.value === current)) {
      els.categorySelect.value = current;
    }
  }

  function populateMatchSelect(payload) {
    if (!els.matchSelect) return;
    const events = payload.events || [];
    lastCatalog.events = events;
    const current = lastTarget.event_ticker || els.matchSelect.value;
    els.matchSelect.innerHTML = '<option value="">All live in this category</option>';
    for (const row of events) {
      const opt = document.createElement("option");
      opt.value = row.event_ticker;
      const kind = row.asset_class === "bitcoin" ? "BTC" : "tennis";
      opt.textContent = `${kind} · ${row.event_name || row.event_ticker}`;
      els.matchSelect.appendChild(opt);
    }
    if (current && Array.from(els.matchSelect.options).some((opt) => opt.value === current)) {
      els.matchSelect.value = current;
    } else {
      els.matchSelect.value = "";
    }
    if (els.matchHint) {
      const label = selectedCategoryLabel();
      els.matchHint.textContent = events.length
        ? `${events.length} live ${events.length === 1 ? "book" : "books"} in ${label}`
        : `No live books in ${label} right now — pick another category or wait for refresh.`;
    }
  }

  async function refreshCatalog() {
    const cat = (els.categorySelect && els.categorySelect.value) || "all";
    if (els.matchHint) els.matchHint.textContent = "Refreshing live markets from Kalshi…";
    try {
      const payload = await fetchJSON(`/api/catalog?category=${encodeURIComponent(cat)}`);
      populateCategorySelect(payload);
      populateMatchSelect(payload);
      paintContractStatus();
    } catch (err) {
      if (els.matchHint) els.matchHint.textContent = err.message;
    }
  }

  function populateContractSelect(payload) {
    // Markets table still refreshes; live match dropdown comes from /api/catalog.
    if (payload && payload.markets) lastMarkets = payload;
  }

  function renderRun(run) {
    running = Boolean(run.running);
    if (running && run.continuous) {
      const suffix = run.stopping ? " · stopping" : "";
      els.runState.textContent = `running cycle ${run.cycles_done}${suffix}`;
    } else if (running) {
      els.runState.textContent = `running ${run.cycles_done}/${run.cycles_total}`;
    } else if (run.last_error) {
      els.runState.textContent = "error";
    } else if (run.finished_at) {
      els.runState.textContent = `idle · last fills ${run.fills_this_run}`;
    } else {
      els.runState.textContent = "idle";
    }
    setRunEnabled(!running);
    renderLog(run.logs);
  }

  function liveIntent() {
    return deskMode === "live";
  }

  function paintStartButton() {
    if (!els.btnStart) return;
    els.btnStart.textContent = liveIntent() ? "Start LIVE (real money)" : "Start";
    els.btnStart.classList.toggle("live-start", liveIntent());
    if (els.btnClearSession) {
      els.btnClearSession.textContent = liveIntent() ? "Clear view" : "Clear";
    }
  }

  function paintDeskChrome(status) {
    const liveOn = deskMode === "live";
    document.body.classList.toggle("desk-paper", !liveOn);
    document.body.classList.toggle("desk-live", liveOn);
    document.querySelectorAll(".live-only").forEach((node) => {
      node.hidden = !liveOn;
    });
    document.querySelectorAll(".paper-only").forEach((node) => {
      node.hidden = liveOn;
    });
    document.title = liveOn ? "Rk Kalshi LIVE Desk" : "Rk Kalshi Paper Desk";
    if (els.btnDeskPaper) {
      els.btnDeskPaper.classList.toggle("is-on", !liveOn);
      els.btnDeskPaper.setAttribute("aria-selected", liveOn ? "false" : "true");
    }
    if (els.btnDeskLive) {
      els.btnDeskLive.classList.toggle("is-on", liveOn);
      els.btnDeskLive.setAttribute("aria-selected", liveOn ? "true" : "false");
    }
    if (els.deskTitle) els.deskTitle.textContent = liveOn ? "LIVE desk — real money" : "Paper desk";
    if (els.modeBanner) {
      els.modeBanner.textContent = liveOn
        ? "LIVE DESK — REAL MONEY — Kalshi orders spend real cash"
        : "PAPER MODE ONLY — paper desk — no live orders";
      els.modeBanner.className = liveOn ? "banner paper-banner is-live" : "banner paper-banner";
    }
    if (els.startEyebrow) els.startEyebrow.textContent = liveOn ? "Live session" : "Paper session";
    if (els.startHeading) els.startHeading.textContent = liveOn ? "Start LIVE trading" : "Start paper trading";
    if (els.startLede) els.startLede.innerHTML = liveOn ? LIVE_LEDE : PAPER_LEDE;
    if (els.bankrollLabel) {
      els.bankrollLabel.textContent = liveOn ? "Kalshi cash (live bankroll)" : "Paper bankroll ($)";
    }
    if (els.signalModeLabel) {
      els.signalModeLabel.textContent = liveOn ? "Signal / research" : "Paper signal";
    }
    if (els.contractHeading) {
      els.contractHeading.textContent = liveOn
        ? "Choose a live book to trade with real money"
        : "Choose a live book to paper-trade";
    }
    if (els.contractCopy) {
      els.contractCopy.textContent = liveOn
        ? "Category first, then a live match or market from Kalshi’s public API. Leave the match blank to trade every live book in that category. Caps still apply."
        : "Category first, then a live match or market from Kalshi’s public API. Leave the match blank to paper-trade every live book in that category.";
    }
    if (els.startPanel) els.startPanel.setAttribute("aria-label", liveOn ? "Start live session" : "Start paper session");
    if (liveOn && lastLiveCaps) clampLiveInputs(lastLiveCaps);
    if (liveOn && lastLiveBalance != null && els.startingCash && !running) {
      els.startingCash.value = lastLiveBalance;
    }
    paintStartButton();
    paintContractStatus();
    if (status && status.live_caps && liveOn) clampLiveInputs(status.live_caps);
  }

  function clampLiveInputs(caps) {
    if (!caps) return;
    const ceiling = Number(caps.max_dollars_hard_ceiling);
    const dailyCeil = Number(caps.daily_loss_hard_ceiling);
    if (els.maxPerTrade && Number.isFinite(ceiling)) {
      const cur = Number(els.maxPerTrade.value);
      if (Number.isFinite(cur) && cur > ceiling) els.maxPerTrade.value = ceiling;
    }
    if (els.dailyLoss && Number.isFinite(dailyCeil)) {
      const cur = Number(els.dailyLoss.value);
      if (Number.isFinite(cur) && cur > dailyCeil) els.dailyLoss.value = dailyCeil;
    }
  }

  function renderAccountStatus(account, defaultEnv, dashboardStatus) {
    const status = (account && account.status) || "disconnected";
    const label = status === "connected" ? "Connected" : status === "error" ? "Error" : "Disconnected";
    if (els.accountConn) {
      els.accountConn.textContent = label;
      els.accountConn.className = `conn ${status}`;
    }
    if (els.accountBanner) {
      els.accountBanner.textContent = account.banner
        || "KALSHI ACCOUNT DISCONNECTED — paper desk only";
      els.accountBanner.className = `banner account-banner is-${status}`;
    }
    if (els.accountPill) {
      els.accountPill.textContent = status === "connected"
        ? `account ${account.environment || ""}`.trim()
        : `account ${status}`;
      els.accountPill.className = status === "connected" ? "pill ok" : status === "error" ? "pill hot" : "pill lock";
    }
    if (els.viewChip) {
      if (status === "connected") {
        const env = (account.environment || "prod") === "demo" ? "demo" : "prod";
        els.viewChip.textContent = `Live account · ${env}`;
        els.viewChip.className = "view-chip live-chip";
      } else {
        els.viewChip.textContent = "Paper desk";
        els.viewChip.className = "view-chip paper-chip";
      }
    }
    if (els.accountHint) {
      const suffix = account.api_key_id_suffix ? ` key ${account.api_key_id_suffix}` : "";
      if (account.message) {
        els.accountHint.textContent = account.message;
      } else if (status === "connected") {
        els.accountHint.textContent = deskMode === "live"
          ? `Connected${suffix}. You are on the LIVE desk. Switch to Paper to disarm.`
          : `Connected${suffix}. Switch to Live only if you intend to spend real money.`;
      } else {
        els.accountHint.textContent =
          "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in a local .env (never paste keys in the UI).";
      }
    }
    const connected = status === "connected";
    if (els.enableLiveTrading) {
      els.enableLiveTrading.disabled = !connected || running;
      els.enableLiveTrading.dataset.locked = connected ? "0" : "1";
      if (!connected) els.enableLiveTrading.checked = false;
    }
    if (els.confirmLiveMoney) {
      const liveChecked = Boolean(els.enableLiveTrading && els.enableLiveTrading.checked);
      els.confirmLiveMoney.disabled = !connected || !liveChecked || running;
      if (!connected || !liveChecked) els.confirmLiveMoney.checked = false;
    }
    if (dashboardStatus && dashboardStatus.live_caps && liveIntent()) {
      clampLiveInputs(dashboardStatus.live_caps);
    }
    paintStartButton();
    if (els.btnDisconnect) els.btnDisconnect.disabled = status === "disconnected" && !account.api_key_id_suffix;
  }

  function emptyLiveTables(message) {
    if (els.livePositionsBody) {
      els.livePositionsBody.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(message)}</td></tr>`;
    }
    if (els.liveFillsBody) {
      els.liveFillsBody.innerHTML = `<tr><td colspan="8" class="empty">${escapeHtml(message)}</td></tr>`;
    }
    if (els.liveOrdersBody) {
      els.liveOrdersBody.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(message)}</td></tr>`;
    }
    if (els.liveBalance) els.liveBalance.textContent = "—";
    if (els.livePortfolio) els.livePortfolio.textContent = "—";
    if (els.livePositions) els.livePositions.textContent = "0";
    if (els.liveFills) els.liveFills.textContent = "0";
  }

  function renderPortfolio(payload) {
    if (payload.account) renderAccountStatus(payload.account);
    const connected = payload.account && payload.account.status === "connected";
    if (els.accountMeta) {
      const env = payload.account && payload.account.environment;
      els.accountMeta.textContent = connected
        ? `${env || "prod"} · ${payload.counts.positions} pos · ${payload.counts.fills} fills · ${payload.counts.orders} orders · ${fmt(payload.latency_ms, 1)} ms`
        : (payload.account && payload.account.message) || "not connected";
    }
    if (payload.balance != null) lastLiveBalance = Number(payload.balance);
    if (els.liveBalance) els.liveBalance.textContent = payload.balance == null ? "—" : `$${fmt(payload.balance, 2)}`;
    if (deskMode === "live" && lastLiveBalance != null && els.startingCash) {
      els.startingCash.value = lastLiveBalance;
    }
    if (els.livePortfolio) els.livePortfolio.textContent = payload.portfolio_value == null ? "—" : `$${fmt(payload.portfolio_value, 2)}`;
    if (els.livePositions) els.livePositions.textContent = String((payload.counts && payload.counts.positions) || 0);
    if (els.liveFills) els.liveFills.textContent = String((payload.counts && payload.counts.fills) || 0);
    if (els.livePosHint) els.livePosHint.textContent = "read-only · not paper";

    const positions = payload.positions || [];
    if (!positions.length) {
      els.livePositionsBody.innerHTML = '<tr><td colspan="7" class="empty">No open Kalshi positions.</td></tr>';
    } else {
      els.livePositionsBody.innerHTML = positions.map((row) => `
        <tr>
          <td class="ticker">${escapeHtml(row.ticker)}</td>
          <td>${escapeHtml(row.side)}</td>
          <td class="num">${fmt(row.contracts, 2)}</td>
          <td class="num">${fmt(row.exposure, 2)}</td>
          <td class="num">${fmt(row.realized_pnl, 2)}</td>
          <td class="num">${fmt(row.fees_paid, 2)}</td>
          <td class="when">${escapeHtml(formatLocalDateTime(row.last_updated))}</td>
        </tr>
      `).join("");
    }

    const fills = payload.fills || [];
    if (!fills.length) {
      els.liveFillsBody.innerHTML = '<tr><td colspan="8" class="empty">No recent Kalshi fills.</td></tr>';
    } else {
      els.liveFillsBody.innerHTML = fills.map((row) => `
        <tr>
          <td class="when">${escapeHtml(formatLocalDateTime(row.created_time))}</td>
          <td class="ticker">${escapeHtml(row.ticker)}</td>
          <td>${escapeHtml(row.book_side || "")}</td>
          <td>${escapeHtml(row.outcome_side || "")}</td>
          <td class="num">${fmt(row.count, 2)}</td>
          <td class="num">${fmt(row.yes_price, 4)}</td>
          <td class="num">${fmt(row.fee, 4)}</td>
          <td>${row.is_taker ? "taker" : "maker"}</td>
        </tr>
      `).join("");
    }

    const orders = payload.orders || [];
    if (!orders.length) {
      els.liveOrdersBody.innerHTML = '<tr><td colspan="7" class="empty">No Kalshi orders returned.</td></tr>';
    } else {
      els.liveOrdersBody.innerHTML = orders.map((row) => `
        <tr>
          <td>${escapeHtml(row.status)}</td>
          <td class="ticker">${escapeHtml(row.ticker)}</td>
          <td>${escapeHtml(row.book_side || "")}</td>
          <td class="num">${fmt(row.remaining, 2)}</td>
          <td class="num">${fmt(row.fill_count, 2)}</td>
          <td class="num">${fmt(row.yes_price, 4)}</td>
          <td class="when">${escapeHtml(formatLocalDateTime(row.created_time))}</td>
        </tr>
      `).join("");
    }

    const errors = payload.errors || {};
    const errText = Object.values(errors).filter(Boolean).join(" · ");
    if (errText && els.accountMeta) {
      els.accountMeta.textContent = errText;
    }
  }

  async function refreshPortfolio() {
    if (els.accountMeta) els.accountMeta.textContent = "loading…";
    try {
      const status = await fetchJSON("/api/account");
      renderAccountStatus(status);
      if (status.status === "disconnected" && !status.api_key_id_suffix) {
        emptyLiveTables("Connect to load live positions.");
        if (els.accountMeta) els.accountMeta.textContent = "not connected";
        return;
      }
      const payload = await fetchJSON("/api/account/portfolio");
      renderPortfolio(payload);
    } catch (err) {
      emptyLiveTables(err.message);
      if (els.accountMeta) els.accountMeta.textContent = err.message;
    }
  }

  async function connectAccount() {
    if (els.btnConnect) els.btnConnect.disabled = true;
    try {
      const payload = await fetchJSON("/api/account/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enable_live_trading: false,
          mode: "paper",
        }),
      });
      renderAccountStatus(payload.account || {});
      await refreshPortfolio();
      updateGateFromAccount(payload.account || {});
    } catch (err) {
      if (els.deskGateError) {
        els.deskGateError.hidden = false;
        els.deskGateError.textContent = err.message;
      }
      renderAccountStatus({
        status: "error",
        banner: "KALSHI ACCOUNT ERROR — paper desk is unchanged; live orders stay disabled",
        message: err.message,
      });
      emptyLiveTables(err.message);
      if (els.accountMeta) els.accountMeta.textContent = err.message;
    } finally {
      if (els.btnConnect) els.btnConnect.disabled = false;
    }
  }

  async function disconnectAccount() {
    if (els.btnDisconnect) els.btnDisconnect.disabled = true;
    try {
      const payload = await fetchJSON("/api/account/disconnect", { method: "POST" });
      renderAccountStatus(payload.account || { status: "disconnected" });
      lastLiveBalance = null;
      if (deskMode === "live") {
        await leaveLiveDesk({ fromDisconnect: true });
      }
      emptyLiveTables("Connect to load live positions.");
      if (els.accountMeta) els.accountMeta.textContent = "disconnected";
    } catch (err) {
      if (els.accountMeta) els.accountMeta.textContent = err.message;
    } finally {
      if (els.btnDisconnect) els.btnDisconnect.disabled = false;
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function refreshStatusBundle() {
    const [status, pnl, fills] = await Promise.all([
      fetchJSON("/api/status"),
      fetchJSON("/api/pnl"),
      fetchJSON("/api/fills"),
    ]);
    renderRun(status.run || {});
    renderSummary(pnl, status);
    renderFills(fills);
  }

  async function refreshMarkets() {
    els.marketsMeta.textContent = "loading…";
    try {
      const payload = await fetchJSON("/api/markets");
      lastMarkets = payload;
      populateContractSelect(payload);
      renderMarkets(payload);
    } catch (err) {
      els.marketsBody.innerHTML =
        `<tr><td colspan="10" class="empty error">${escapeHtml(err.message)}</td></tr>`;
      els.marketsMeta.textContent = "Kalshi request failed";
    }
  }

  async function startRun(cycles) {
    const sleep = Number(els.sleep.value);
    setRunEnabled(false);
    try {
      const run = await fetchJSON("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cycles,
          sleep_s: Number.isFinite(sleep) ? sleep : 15,
          mode: "paper",
        }),
      });
      renderRun(run);
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
      setRunEnabled(true);
    }
  }

  async function startSession() {
    const startingCash = Number(els.startingCash.value);
    const maxPerTrade = Number(els.maxPerTrade.value);
    const dailyLoss = Number(els.dailyLoss.value);
    const sleep = Number(els.startSleep.value);
    setRunEnabled(false);
    try {
      const payload = await fetchJSON("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          starting_cash: startingCash,
          max_dollars_per_ticker: maxPerTrade,
          daily_loss_limit: dailyLoss,
          sleep_s: Number.isFinite(sleep) ? sleep : 15,
          continuous: true,
          live_matches_only: Boolean(els.liveMatchesOnly && els.liveMatchesOnly.checked),
          signal_mode: (els.signalMode && els.signalMode.value) || "hybrid",
          trade_style: (els.tradeStyle && els.tradeStyle.value) || "active",
          category_id: (els.categorySelect && els.categorySelect.value) || "all",
          ...contractStartFields(),
          ...categoryTradeFlags(),
          live: liveIntent(),
          confirm_live: liveIntent(),
          understand_real_money: liveIntent(),
          mode: liveIntent() ? "live" : "paper",
        }),
      });
      if (payload.run) renderRun(payload.run);
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
      contractError = err.message;
      paintContractStatus();
      setRunEnabled(true);
    }
  }

  async function stopSession() {
    els.btnStop.disabled = true;
    try {
      const run = await fetchJSON("/api/stop", { method: "POST" });
      renderRun(run);
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
    } finally {
      els.btnStop.disabled = false;
    }
  }

  function categoryTradeFlags() {
    const cat = (els.categorySelect && els.categorySelect.value) || "all";
    const kind = categoryKind(cat) || (cat === "all" ? "all" : cat.startsWith("btc") ? "bitcoin" : "tennis");
    return {
      trade_tennis: kind === "all" || kind === "tennis",
      trade_bitcoin: kind === "all" || kind === "bitcoin",
    };
  }

  function contractStartFields() {
    const pasted = (els.contractUrl && els.contractUrl.value.trim()) || "";
    const selected = (els.matchSelect && els.matchSelect.value) || "";
    if (pasted) return { target_url: pasted };
    if (selected) return { target_event_ticker: selected };
    return {};
  }

  async function useContract() {
    const url = (els.contractUrl && els.contractUrl.value.trim()) || "";
    const eventTicker = (els.matchSelect && els.matchSelect.value) || "";
    contractError = "";
    try {
      const payload = await fetchJSON("/api/contract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(url ? { url, mode: "paper" } : { event_ticker: eventTicker, mode: "paper" }),
      });
      renderTarget(payload.target || {});
    } catch (err) {
      contractError = err.message;
      paintContractStatus();
    }
  }

  async function clearContract() {
    contractError = "";
    try {
      const payload = await fetchJSON("/api/contract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "paper" }),
      });
      if (els.contractUrl) els.contractUrl.value = "";
      renderTarget(payload.target || {});
    } catch (err) {
      contractError = err.message;
      paintContractStatus();
    }
  }

  async function clearSession() {
    if (els.btnClearSession) els.btnClearSession.disabled = true;
    contractError = "";
    try {
      const url = deskMode === "live" ? "/api/clear-view" : "/api/clear";
      const payload = await fetchJSON(url, { method: "POST" });
      if (deskMode !== "live" && els.contractUrl) els.contractUrl.value = "";
      if (payload.run) renderRun(payload.run);
      if (payload.target) renderTarget(payload.target);
      await refreshStatusBundle();
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
    } finally {
      if (els.btnClearSession) els.btnClearSession.disabled = false;
    }
  }

  async function cancelOpenOrders() {
    if (els.btnCancelOpen) els.btnCancelOpen.disabled = true;
    try {
      const payload = await fetchJSON("/api/live/cancel-open", { method: "POST" });
      els.log.textContent = payload.note || "Cancel open Kalshi orders requested.";
      await refreshPortfolio();
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
    } finally {
      if (els.btnCancelOpen) els.btnCancelOpen.disabled = false;
    }
  }

  els.btnOnce.addEventListener("click", () => startRun(1));
  els.btnCycles.addEventListener("click", () => {
    const cycles = Math.max(1, Number(els.cycles.value) || 1);
    startRun(cycles);
  });
  async function clearLogs() {
    try {
      const run = await fetchJSON("/api/logs/clear", { method: "POST" });
      renderRun(run);
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
    }
  }

  els.btnStart.addEventListener("click", () => startSession());
  els.btnStop.addEventListener("click", () => stopSession());
  if (els.btnClearSession) {
    els.btnClearSession.addEventListener("click", () => clearSession());
  }
  if (els.btnUseContract) {
    els.btnUseContract.addEventListener("click", () => useContract());
  }
  if (els.btnClearContract) {
    els.btnClearContract.addEventListener("click", () => clearContract());
  }
  if (els.categorySelect) {
    els.categorySelect.addEventListener("change", () => {
      if (els.matchSelect) els.matchSelect.value = "";
      lastTarget = { active: false, event_ticker: "", market_ticker: "", label: "", asset_class: "" };
      refreshCatalog();
    });
  }
  if (els.matchSelect) {
    els.matchSelect.addEventListener("change", () => {
      const picked = els.matchSelect.value;
      if (!picked) {
        lastTarget = { active: false, event_ticker: "", market_ticker: "", label: "", asset_class: "" };
        paintContractStatus();
        return;
      }
      useContract();
    });
  }
  if (els.tradeStyle) {
    els.tradeStyle.addEventListener("change", () => {
      applyTradeStyleDefaults();
      if (liveIntent()) {
        fetchJSON("/api/status").then((status) => {
          if (status.live_caps) clampLiveInputs(status.live_caps);
        }).catch(() => {});
      }
    });
  }
  async function syncLiveArm() {
    if (deskMode !== "live") return;
    const stillLive = Boolean(
      els.enableLiveTrading && els.enableLiveTrading.checked
      && els.confirmLiveMoney && els.confirmLiveMoney.checked
    );
    if (!stillLive) await leaveLiveDesk();
  }

  function showGateError(message) {
    if (!els.deskGateError) return;
    if (!message) {
      els.deskGateError.hidden = true;
      els.deskGateError.textContent = "";
      return;
    }
    els.deskGateError.hidden = false;
    els.deskGateError.textContent = message;
  }

  function gateConfirmed() {
    return Boolean(
      els.gateEnableLive && els.gateEnableLive.checked
      && els.gateConfirmLive && els.gateConfirmLive.checked
    );
  }

  function paintGateEnter() {
    if (!els.btnGateEnter) return;
    const connected = els.deskGateConfirm && !els.deskGateConfirm.hidden;
    els.btnGateEnter.disabled = !(connected && gateConfirmed());
  }

  function updateGateFromAccount(account) {
    const connected = account && account.status === "connected";
    if (els.deskGateConnect) els.deskGateConnect.hidden = Boolean(connected);
    if (els.deskGateConfirm) els.deskGateConfirm.hidden = !connected;
    if (connected && els.deskGateBalance) {
      const env = account.environment || "";
      const suffix = account.api_key_id_suffix ? ` key ${account.api_key_id_suffix}` : "";
      const cash = lastLiveBalance != null ? ` · cash $${fmt(lastLiveBalance, 2)}` : "";
      els.deskGateBalance.textContent =
        `Connected${suffix}${env ? ` · ${env}` : ""}${cash}. Confirm both boxes to enter the Live desk.`;
    }
    paintGateEnter();
  }

  function openLiveGate() {
    if (!els.deskGate) return;
    showGateError("");
    if (els.gateEnableLive) els.gateEnableLive.checked = false;
    if (els.gateConfirmLive) els.gateConfirmLive.checked = false;
    els.deskGate.hidden = false;
    fetchJSON("/api/account").then((account) => {
      updateGateFromAccount(account);
    }).catch((err) => {
      updateGateFromAccount({ status: "disconnected" });
      showGateError(err.message);
    });
    paintGateEnter();
  }

  function closeLiveGate() {
    if (els.deskGate) els.deskGate.hidden = true;
    showGateError("");
  }

  async function enterLiveDesk(opts) {
    const skipConfirm = Boolean(opts && opts.skipConfirm);
    try {
      if (!skipConfirm) {
        if (!gateConfirmed()) {
          showGateError("Check both confirmation boxes to enter the Live desk.");
          return;
        }
        const payload = await fetchJSON("/api/live", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: true,
            confirm_live: true,
            understand_real_money: true,
          }),
        });
        if (payload.live_caps) lastLiveCaps = payload.live_caps;
        if (payload.account) renderAccountStatus(payload.account, null, payload);
      }
      deskMode = "live";
      if (els.enableLiveTrading) els.enableLiveTrading.checked = true;
      if (els.confirmLiveMoney) els.confirmLiveMoney.checked = true;
      closeLiveGate();
      paintDeskChrome();
      await refreshPortfolio().catch(() => {});
      await refreshStatusBundle().catch(() => {});
    } catch (err) {
      showGateError(err.message);
      deskMode = "paper";
      paintDeskChrome();
    }
  }

  async function leaveLiveDesk(opts) {
    const fromDisconnect = Boolean(opts && opts.fromDisconnect);
    try {
      if (running && !fromDisconnect) {
        els.log.textContent = "Stop the LIVE session before switching to Paper.";
        paintDeskChrome();
        return;
      }
      if (!fromDisconnect) {
        await fetchJSON("/api/live", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: false }),
        });
      }
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
    }
    deskMode = "paper";
    if (els.enableLiveTrading) els.enableLiveTrading.checked = false;
    if (els.confirmLiveMoney) els.confirmLiveMoney.checked = false;
    if (els.gateEnableLive) els.gateEnableLive.checked = false;
    if (els.gateConfirmLive) els.gateConfirmLive.checked = false;
    paintDeskChrome();
    await refreshStatusBundle().catch(() => {});
  }

  function requestLiveDesk() {
    if (deskMode === "live") {
      paintDeskChrome();
      return;
    }
    if (running) {
      els.log.textContent = "Stop the paper session before switching to Live.";
      paintDeskChrome();
      return;
    }
    openLiveGate();
    paintDeskChrome();
  }

  async function bootDesk() {
    paintDeskChrome();
    try {
      const status = await fetchJSON("/api/status");
      const liveRunning = Boolean(status.run && status.run.running && status.live_enabled);
      if (liveRunning) {
        deskMode = "live";
        if (els.enableLiveTrading) els.enableLiveTrading.checked = true;
        if (els.confirmLiveMoney) els.confirmLiveMoney.checked = true;
        if (status.live_caps) lastLiveCaps = status.live_caps;
        paintDeskChrome(status);
        return;
      }
      if (status.live_armed || status.live_enabled) {
        await fetchJSON("/api/live", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: false }),
        });
      }
      deskMode = "paper";
      paintDeskChrome(status);
    } catch (err) {
      deskMode = "paper";
      paintDeskChrome();
      els.log.textContent = `error: ${err.message}`;
    }
  }

  if (els.enableLiveTrading) {
    els.enableLiveTrading.addEventListener("change", () => { syncLiveArm(); });
  }
  if (els.confirmLiveMoney) {
    els.confirmLiveMoney.addEventListener("change", () => { syncLiveArm(); });
  }
  if (els.btnDeskPaper) {
    els.btnDeskPaper.addEventListener("click", () => { leaveLiveDesk(); });
  }
  if (els.btnDeskLive) {
    els.btnDeskLive.addEventListener("click", () => { requestLiveDesk(); });
  }
  if (els.btnGateCancel) {
    els.btnGateCancel.addEventListener("click", () => {
      closeLiveGate();
      deskMode = "paper";
      paintDeskChrome();
    });
  }
  if (els.btnGateConnect) {
    els.btnGateConnect.addEventListener("click", () => { connectAccount(); });
  }
  if (els.btnGateEnter) {
    els.btnGateEnter.addEventListener("click", () => { enterLiveDesk(); });
  }
  if (els.gateEnableLive) {
    els.gateEnableLive.addEventListener("change", () => { paintGateEnter(); });
  }
  if (els.gateConfirmLive) {
    els.gateConfirmLive.addEventListener("change", () => { paintGateEnter(); });
  }
  if (els.btnCancelOpen) {
    els.btnCancelOpen.addEventListener("click", () => { cancelOpenOrders(); });
  }
  if (els.btnClearLogs) {
    els.btnClearLogs.addEventListener("click", () => clearLogs());
  }
  els.btnMarkets.addEventListener("click", () => { refreshMarkets(); });
  if (els.filterLiveMarkets) {
    els.filterLiveMarkets.addEventListener("change", () => {
      if (lastMarkets) renderMarkets(lastMarkets);
    });
  }
  if (els.filterBitcoinMarkets) {
    els.filterBitcoinMarkets.addEventListener("change", () => {
      if (lastMarkets) renderMarkets(lastMarkets);
    });
  }
  if (els.btnConnect) els.btnConnect.addEventListener("click", () => connectAccount());
  if (els.btnDisconnect) els.btnDisconnect.addEventListener("click", () => disconnectAccount());
  if (els.btnAccountRefresh) {
    els.btnAccountRefresh.addEventListener("click", () => { refreshPortfolio(); });
  }
  if (els.autoAccount) {
    els.autoAccount.addEventListener("change", () => {
      if (accountTimer) {
        clearInterval(accountTimer);
        accountTimer = null;
      }
      if (els.autoAccount.checked) {
        accountTimer = setInterval(refreshPortfolio, 15000);
      }
    });
  }
  els.autoMarkets.addEventListener("change", () => {
    if (marketsTimer) {
      clearInterval(marketsTimer);
      marketsTimer = null;
    }
    if (els.autoMarkets.checked) {
      marketsTimer = setInterval(() => {
        refreshMarkets();
        refreshCatalog();
      }, 15000);
    }
  });

  setInterval(() => {
    refreshStatusBundle().catch((err) => {
      els.runState.textContent = err.message;
    });
  }, pollMs);

  applyLocalTimeHeaders();
  fetchJSON("/api/presets").then((payload) => {
    stylePresets = payload || stylePresets;
    paintTradeStyleHint();
  }).catch(() => {});
  bootDesk().then(() => refreshStatusBundle()).catch((err) => {
    els.log.textContent = `error: ${err.message}`;
  });
  refreshMarkets();
  refreshCatalog();
  refreshPortfolio().catch(() => {});
})();
