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
    startHint: $("start-hint"),
    liveMatchesOnly: $("live-matches-only"),
    tradeBitcoin: $("trade-bitcoin"),
    tradeTennis: $("trade-tennis"),
    filterBitcoinMarkets: $("filter-bitcoin-markets"),
    filterLiveMarkets: $("filter-live-markets"),
    log: $("log"),
    logCount: $("log-count"),
    marketsBody: $("markets-body"),
    marketsMeta: $("markets-meta"),
    autoMarkets: $("auto-markets"),
    btnMarkets: $("btn-markets"),
    fillsBody: $("fills-body"),
    fillsMeta: $("fills-meta"),
    footer: $("footer-meta"),
  };

  let running = false;
  let marketsTimer = null;
  let lastMarkets = null;
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

  function formatLocalDateTime(value) {
    if (value == null || value === "") return "";
    const text = String(value).trim();
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) return text;
    return parsed.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
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
    els.startingCash.disabled = !enabled;
    els.maxPerTrade.disabled = !enabled;
    els.dailyLoss.disabled = !enabled;
    els.startSleep.disabled = !enabled;
    if (els.liveMatchesOnly) els.liveMatchesOnly.disabled = !enabled;
    if (els.tradeBitcoin) els.tradeBitcoin.disabled = !enabled;
    if (els.tradeTennis) els.tradeTennis.disabled = !enabled;
    els.startPanel.classList.toggle("is-running", !enabled);
  }

  function renderLog(lines) {
    if (!lines || !lines.length) {
      els.log.textContent = running ? "Paper-run started…" : "Waiting for a paper-run…";
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
      if (m.asset_class === "bitcoin") return showBitcoin;
      if (liveOnly) return Boolean(m.in_play);
      return true;
    });
    els.marketsMeta.textContent =
      `${btcCount} btc · ${liveCount} live tennis / ${payload.count} open · ${payload.series.join(", ")} · ${fmt(payload.latency_ms, 1)} ms`;
    if (!all.length) {
      els.marketsBody.innerHTML =
        '<tr><td colspan="10" class="empty">No open markets (empty series filter).</td></tr>';
      return;
    }
    if (!rows.length) {
      els.marketsBody.innerHTML =
        '<tr><td colspan="10" class="empty">No rows for the current Bitcoin / live-tennis filters.</td></tr>';
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
        <td class="num">${fmt(m.volume, 1)}</td>
      </tr>
    `).join("");
  }

  function renderFills(payload) {
    const rows = payload.fills || [];
    els.fillsMeta.textContent = `${rows.length} fill${rows.length === 1 ? "" : "s"} · locked schema · newest first`;
    if (!rows.length) {
      els.fillsBody.innerHTML =
        '<tr><td colspan="11" class="empty">No paper fills yet — run a cycle.</td></tr>';
      return;
    }
    els.fillsBody.innerHTML = rows.map((f) => `
      <tr>
        <td class="ticker">${escapeHtml(formatLocalDateTime(f.timestamp))}</td>
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
    els.footer.textContent =
      `localhost · paper_mode=true · live.enabled=false · btc=${status.trade_bitcoin} tennis=${status.trade_tennis} · live_matches_only=${status.live_matches_only} · series ${series} · edge ${status.edge_threshold_cents}¢`;
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
      if (els.tradeBitcoin && status.trade_bitcoin != null) {
        els.tradeBitcoin.checked = Boolean(status.trade_bitcoin);
      }
      if (els.tradeTennis && status.trade_tennis != null) {
        els.tradeTennis.checked = Boolean(status.trade_tennis);
      }
      els.startForm.dataset.seeded = "1";
      els.sleep.dataset.seeded = "1";
    }
    const books = [
      status.trade_bitcoin ? "Bitcoin buy+sell" : null,
      status.trade_tennis ? (status.live_matches_only ? "live tennis" : "all tennis") : null,
    ].filter(Boolean).join(" · ") || "no books selected";
    els.startHint.textContent = running
      ? `Paper session running (${books}) — Stop ends polling. Live orders stay disabled.`
      : `Paper bankroll $${fmt(status.starting_cash, 0)} · max $${fmt(status.max_dollars_per_ticker, 0)}/trade · daily loss $${fmt(status.daily_loss_limit, 0)} · ${books}`;
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
          trade_bitcoin: Boolean(!els.tradeBitcoin || els.tradeBitcoin.checked),
          trade_tennis: Boolean(!els.tradeTennis || els.tradeTennis.checked),
          mode: "paper",
        }),
      });
      if (payload.run) renderRun(payload.run);
    } catch (err) {
      els.log.textContent = `error: ${err.message}`;
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

  els.btnOnce.addEventListener("click", () => startRun(1));
  els.btnCycles.addEventListener("click", () => {
    const cycles = Math.max(1, Number(els.cycles.value) || 1);
    startRun(cycles);
  });
  els.btnStart.addEventListener("click", () => startSession());
  els.btnStop.addEventListener("click", () => stopSession());
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
  els.autoMarkets.addEventListener("change", () => {
    if (marketsTimer) {
      clearInterval(marketsTimer);
      marketsTimer = null;
    }
    if (els.autoMarkets.checked) {
      marketsTimer = setInterval(refreshMarkets, 15000);
    }
  });

  setInterval(() => {
    refreshStatusBundle().catch((err) => {
      els.runState.textContent = err.message;
    });
  }, pollMs);

  refreshStatusBundle().catch((err) => {
    els.log.textContent = `error: ${err.message}`;
  });
  refreshMarkets();
})();
