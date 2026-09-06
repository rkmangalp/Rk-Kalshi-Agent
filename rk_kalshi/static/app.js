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
  }

  function renderLog(lines) {
    if (!lines || !lines.length) {
      els.log.textContent = running ? "Paper-run started…" : "Waiting for a paper-run…";
      els.logCount.textContent = "0 lines";
      return;
    }
    els.log.textContent = lines.join("\n");
    els.log.scrollTop = els.log.scrollHeight;
    els.logCount.textContent = `${lines.length} line${lines.length === 1 ? "" : "s"}`;
  }

  function renderMarkets(payload) {
    const rows = payload.markets || [];
    els.marketsMeta.textContent =
      `${payload.count} open · ${payload.series.join(", ")} · ${fmt(payload.latency_ms, 1)} ms`;
    if (!rows.length) {
      els.marketsBody.innerHTML =
        '<tr><td colspan="8" class="empty">No open tennis markets (off-season or empty series filter).</td></tr>';
      return;
    }
    els.marketsBody.innerHTML = rows.map((m) => `
      <tr>
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
        <td class="ticker">${escapeHtml(f.timestamp || "")}</td>
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
      `localhost · paper_mode=true · live.enabled=false · series ${series} · edge ${status.edge_threshold_cents}¢`;
    if (!els.sleep.dataset.seeded && status.cycle_sleep_s != null) {
      els.sleep.value = status.cycle_sleep_s;
      els.sleep.dataset.seeded = "1";
    }
  }

  function renderRun(run) {
    running = Boolean(run.running);
    if (running) {
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
    renderSummary(pnl, status);
    renderRun(status.run || {});
    renderFills(fills);
  }

  async function refreshMarkets() {
    els.marketsMeta.textContent = "loading…";
    try {
      const payload = await fetchJSON("/api/markets");
      renderMarkets(payload);
    } catch (err) {
      els.marketsBody.innerHTML =
        `<tr><td colspan="8" class="empty error">${escapeHtml(err.message)}</td></tr>`;
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

  els.btnOnce.addEventListener("click", () => startRun(1));
  els.btnCycles.addEventListener("click", () => {
    const cycles = Math.max(1, Number(els.cycles.value) || 1);
    startRun(cycles);
  });
  els.btnMarkets.addEventListener("click", () => { refreshMarkets(); });
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
