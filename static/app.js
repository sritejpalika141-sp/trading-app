/* ===== NIFTY Trading Dashboard — Frontend Logic ===== */

// IST offset: +5:30 = 19800 seconds
const IST_OFFSET = 19800;

// State
let chart5m = null;
let series5m = null;
let analysisData = null;
let refreshInterval = null;
let markers1h = [], markers5m = [];
let obZoneSeries = [];  // OB rectangle zone series
let fvgZoneSeries = []; // FVG rectangle zone series
let lastCandleTime5m = 0; // Track latest 5m candle time for zone extension

// Live spot price lines
let liveSpotLine1h = null;
let liveSpotLine5m = null;

// ===== INITIALIZATION =====
let wsConnection = null;
let analysisInterval = null;

// Theme state
let isLightMode = localStorage.getItem('theme') === 'light';

function toggleTheme() {
  isLightMode = !isLightMode;
  localStorage.setItem('theme', isLightMode ? 'light' : 'dark');
  applyTheme();
}

function applyTheme() {
  if (isLightMode) {
    document.body.classList.add('light-mode');
    document.getElementById('themeToggleBtn').textContent = '🌙';
  } else {
    document.body.classList.remove('light-mode');
    document.getElementById('themeToggleBtn').textContent = '☀️';
  }

  // Update chart theme if it exists
  if (chart5m) {
    chart5m.applyOptions({
      layout: {
        background: { type: 'solid', color: isLightMode ? '#ffffff' : '#1a1f2e' },
        textColor: isLightMode ? '#475569' : '#94a3b8',
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false }
      },
      rightPriceScale: { borderColor: isLightMode ? '#cbd5e1' : '#2a3144' },
      timeScale: { borderColor: isLightMode ? '#cbd5e1' : '#2a3144' },
    });
  }
}
let activeSymbol = "NSE:NIFTY50-INDEX";
let activeScripts = [];
let lastMarketData = {};

async function selectScript(symbol) {
  activeSymbol = symbol;
  showToast(`Switching chart to ${symbol.replace('NSE:','')}`, 'info');
  
  // Highlight the row in UI
  renderScriptsList();
  
  // Update header immediately
  const spotData = lastMarketData[symbol];
  if (spotData) {
      updateSpotLive({
          symbol: symbol,
          spot: spotData.lp,
          change: spotData.change,
          change_pct: spotData.change_pct,
          vix: lastMarketData.vix?.lp || 0,
          vix_change: lastMarketData.vix?.change || 0
      });
  }

  // Refresh data
  await fetchAnalysis();
  await fetchCandles();
}

async function fetchScripts() {
  try {
    const res = await fetch('/api/scripts');
    const data = await res.json();
    activeScripts = data.scripts || [];
    renderScriptsList();
  } catch (e) {
    console.error("Failed to fetch scripts:", e);
  }
}

function renderScriptsList(liveData = null) {
  const container = document.getElementById('scriptList');
  if (!container) return;
  
  if (liveData) lastMarketData = liveData;
  const dataMap = lastMarketData || {};
  
  if (!activeScripts || activeScripts.length === 0) {
    container.innerHTML = '<tr><td colspan="5" style="padding:20px; text-align:center; color:var(--text-muted);">No active scrips.</td></tr>';
    return;
  }
  
  container.innerHTML = activeScripts.map(s => {
    const d = dataMap[s] || {};
    const lp = d.lp || 0;
    const ch = d.change || 0;
    const chp = d.change_pct || 0;
    const isUp = ch >= 0;
    const color = isUp ? 'var(--bullish)' : 'var(--bearish)';
    const sign = isUp ? '+' : '';
    const isSelected = s === activeSymbol;
    const trendClass = lp > 0 ? (isUp ? 'up' : 'down') : 'flat';
    
    return `
      <tr class="mw-row ${isSelected ? 'selected' : ''}" onclick="selectScript('${s}')">
        <td style="padding:8px 12px; font-weight:500; font-size:12px;"><span class="mw-trend-dot ${trendClass}"></span>${s.replace('NSE:', '')}</td>
        <td style="padding:8px 12px; text-align:right; font-weight:500; color:${color}; font-size:12px; font-family:'JetBrains Mono',monospace;">${lp > 0 ? lp.toFixed(2) : '--'}</td>
        <td style="padding:8px 12px; text-align:right; color:${color}; font-size:11px; font-family:'JetBrains Mono',monospace;">${lp > 0 ? sign + ch.toFixed(2) : '--'}</td>
        <td style="padding:8px 12px; text-align:right; color:${color}; font-size:11px; font-family:'JetBrains Mono',monospace;">${lp > 0 ? sign + chp.toFixed(2) + '%' : '--'}</td>
        <td style="padding:8px 12px; text-align:center;" onclick="event.stopPropagation()">
          <span onclick="removeScript('${s}')" style="cursor:pointer; color:#666; font-size:13px; opacity:0.6; transition:opacity 0.2s;" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.6">×</span>
        </td>
      </tr>
    `;
  }).join('');
  
  // Also update symbol tabs
  renderSymbolTabs();
}

async function addScript() {
  const input = document.getElementById('newScript');
  const symbol = input.value.trim().toUpperCase();
  if (!symbol) return;
  
  showToast(`Adding ${symbol}...`, 'info');
  try {
    const res = await fetch('/api/scripts/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol })
    });
    const data = await res.json();
    if (data.success) {
      activeScripts = data.scripts || [];
      renderScriptsList();
      input.value = '';
      showToast('Scrip added', 'success');
    } else {
      showToast('Failed to add: ' + data.message, 'error');
    }
  } catch (e) {
    showToast('Error adding scrip', 'error');
  }
}

async function removeScript(symbol) {
  if (!confirm(`Remove ${symbol}?`)) return;
  
  showToast(`Removing ${symbol}...`, 'info');
  try {
    const res = await fetch('/api/scripts/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol })
    });
    const data = await res.json();
    if (data.success) {
      activeScripts = data.scripts || [];
      renderScriptsList();
      showToast('Scrip removed', 'success');
    }
  } catch (e) {
    showToast('Error removing scrip', 'error');
  }
}

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
  applyTheme();
  loadVisibilityPrefs();
  fetchScripts();
  fetchSignalHistory();
  initCharts();
  
  try {
    const res = await fetch('/api/user-config');
    const config = await res.json();
    if (config.theme) document.body.setAttribute('data-theme', config.theme);
  } catch(e) {}

  try {
    // fetchAnalysis returns candles_5m so no need for separate fetchCandles call
    // (the /api/candles endpoint is rate-limited since analysis already fetches from Fyers)
    await fetchAnalysis();
  } catch (e) {
    console.error('Initial data fetch failed:', e);
    showToast('Dashboard starting with partial data...', 'info');
  }
  
  connectWebSocket();
  
  // Refresh analysis every 45 seconds (includes candle data)
  analysisInterval = setInterval(async () => {
    try {
      await fetchAnalysis();
    } catch (e) {
      console.error('Interval refresh failed:', e);
    }
  }, 45000);

  // Start core loops
  fetchAutomationStatus();
  setInterval(checkAuthStatus, 20000);
  setInterval(fetchAutomationStatus, 15000); // Fast update for automation stats
  
  fetchSignalHistory();
  setInterval(fetchSignalHistory, 20000);

  fetchFunds();
  setInterval(fetchFunds, 60000);

  // Fetch version info
  fetchVersion();
});

async function fetchAutomationStatus() {
  try {
    const resp = await fetch('/api/automation');
    const data = await resp.json();
    
    document.getElementById('autoToggle').checked = data.enabled;
    document.getElementById('autoTrades').textContent = `${data.trades_today}/${data.max_trades} Trades`;
    
    const pnlEl = document.getElementById('autoPnl');
    const pnl = data.pnl_today || 0;
    const sign = pnl > 0 ? '+' : (pnl < 0 ? '-' : '');
    pnlEl.textContent = `${sign}₹${Math.abs(pnl).toLocaleString('en-IN', {minimumFractionDigits: 2})}`;
    pnlEl.className = pnl > 0 ? 'pnl-positive' : (pnl < 0 ? 'pnl-negative' : '');
    
    // Label update
    const label = document.querySelector('.auto-label');
    label.style.color = data.enabled ? 'var(--accent-blue)' : 'var(--text-muted)';
    label.textContent = data.enabled ? 'AUTO ACTIVE' : 'AUTO OFF';
  } catch (e) {}
}

async function toggleAutomation() {
  const enabled = document.getElementById('autoToggle').checked;
  const resp = await fetch(`/api/automation/toggle?enabled=${enabled}`, { method: 'POST' });
  const data = await resp.json();
  if (data.success) {
    showToast(`Automation ${enabled ? 'ENABLED' : 'DISABLED'}`, enabled ? 'success' : 'info');
  }
}
async function checkAuthStatus() {
  try {
    const resp = await fetch('/api/auth-status');
    const data = await resp.json();
    updateAuthUI(data.authenticated);
  } catch (e) {}
}

function updateAuthUI(authState) {
  const btn = document.getElementById('loginBtn');
  const connDot = document.getElementById('connDot');
  const connText = document.getElementById('connText');
  const footerConn = document.getElementById('footerConn');
  
  if (authState === 'connecting') {
    btn.style.display = 'none';
    if (connDot) { connDot.className = 'conn-dot degraded'; }
    if (connText) { connText.textContent = 'Connecting...'; }
    if (footerConn) { footerConn.textContent = 'WebSocket: Connecting'; }
  } else if (authState === false) {
    btn.style.display = 'block';
    if (connDot) { connDot.className = 'conn-dot disconnected'; }
    if (connText) { connText.textContent = 'Disconnected'; }
    if (footerConn) { footerConn.textContent = 'WebSocket: Disconnected'; }
  } else if (authState === true) {
    btn.style.display = 'none';
    if (connDot) { connDot.className = 'conn-dot excellent'; }
    if (connText) { connText.textContent = 'Live'; }
    if (footerConn) { footerConn.textContent = 'WebSocket: Connected'; }
  }
}

function closeAuthModal() {
  document.getElementById('authModal').classList.add('hidden');
}

async function triggerLogin() {
  showToast('Generating Fyers Login URL...', 'info');
  try {
    const resp = await fetch('/api/login');
    const data = await resp.json();
    if (data.success && data.url) {
      window.open(data.url, '_blank');
      // Show the modal
      document.getElementById('authModal').classList.remove('hidden');
      document.getElementById('authUrlInput').value = '';
      document.getElementById('authUrlInput').focus();
      showToast('Login window opened. Please complete login and paste the redirect URL.', 'info');
    } else {
      showToast('Failed to generate login URL: ' + (data.message || 'Unknown error'), 'error');
    }
  } catch (e) {
    console.error('Login error:', e);
    showToast('Failed to initiate login flow', 'error');
  }
}

async function submitAuthCode() {
  const input = document.getElementById('authUrlInput').value.trim();
  if (!input) {
    showToast('Please paste the redirect URL or auth code', 'warning');
    return;
  }

  showToast('Exchanging token... Please wait', 'info');
  try {
    const resp = await fetch('/api/submit-auth-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: input })
    });
    const data = await resp.json();
    if (data.success) {
      showToast('Fyers Account Connected!', 'success');
      closeAuthModal();
      // Status will be updated via WebSocket broadcast
    } else {
      showToast('Connection failed: ' + (data.message || 'Check your code'), 'error');
    }
  } catch (e) {
    console.error('Submission error:', e);
    showToast('Failed to connect. Try again.', 'error');
  }
}

function initCharts() {
  const chartOptions = {
    layout: {
      background: { color: isLightMode ? '#ffffff' : '#1a1f2e' },
      textColor: isLightMode ? '#475569' : '#94a3b8',
      fontFamily: 'Inter, sans-serif',
      fontSize: 11,
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { visible: false },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: '#3b82f640', width: 1, style: 2 },
      horzLine: { color: '#3b82f640', width: 1, style: 2 },
    },
    rightPriceScale: {
      borderColor: isLightMode ? '#cbd5e1' : '#2a3144',
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderColor: isLightMode ? '#cbd5e1' : '#2a3144',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 5,
      barSpacing: 8,
    },
    localization: {
      priceFormatter: price => price.toFixed(2),
      timeFormatter: (time) => {
        // Since we add IST_OFFSET to the timestamp, we treat it as UTC to avoid double-shift
        const date = new Date(time * 1000);
        return date.getUTCHours().toString().padStart(2, '0') + ':' + 
               date.getUTCMinutes().toString().padStart(2, '0');
      },
    },
    handleScroll: { vertTouchDrag: false },
  };

  // 5M Master Chart
  const el5m = document.getElementById('chart5m');
  chart5m = LightweightCharts.createChart(el5m, { ...chartOptions, width: el5m.clientWidth, height: el5m.clientHeight });
  series5m = chart5m.addCandlestickSeries({
    upColor: '#10b981', downColor: '#ef4444',
    borderUpColor: '#10b981', borderDownColor: '#ef4444',
    wickUpColor: '#10b98188', wickDownColor: '#ef444488',
  });

  // Resize handler
  const resizeObserver = new ResizeObserver(entries => {
    for (const entry of entries) {
      const { width, height } = entry.contentRect;
      if (entry.target.id === 'chart5m') chart5m.resize(width, height);
    }
  });
  resizeObserver.observe(el5m);

  // === CROSSHAIR HOVER OHLC LEGEND ===
  function makeLegendHandler(chart, series, legendId) {
    const legendEl = document.getElementById(legendId);
    chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) {
        legendEl.innerHTML = 'O: -- H: -- L: -- C: --';
        return;
      }
      const d = param.seriesData.get(series);
      if (!d) return;
      const o = d.open?.toFixed(2) || '--';
      const h = d.high?.toFixed(2) || '--';
      const l = d.low?.toFixed(2) || '--';
      const c = d.close?.toFixed(2) || '--';
      const color = d.close >= d.open ? 'lg-up' : 'lg-down';
      legendEl.innerHTML = `O: <span class="${color}">${o}</span>  H: <span class="${color}">${h}</span>  L: <span class="${color}">${l}</span>  C: <span class="${color}">${c}</span>`;
    });
  }
  makeLegendHandler(chart5m, series5m, 'legend5m');
}


// ===== WEBSOCKET STREAMING =====
function connectWebSocket() {
  const wsUrl = `ws://${window.location.host}/ws/live`;
  console.log('📡 Connecting WebSocket:', wsUrl);
  
  updateAuthUI('connecting'); // Show connecting state immediately

  wsConnection = new WebSocket(wsUrl);

  wsConnection.onopen = () => {
    console.log('📡 WebSocket connected');
    // We wait for auth_status message before setting Live status
  };

  wsConnection.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
      case 'auth_status':
        updateAuthUI(data.authenticated);
        break;
      case 'spot':
        updateSpotLive(data);
        break;
      case 'positions':
        renderPositions(data.positions);
        // Update P&L in account section
        updatePnlDisplays(data.total_pnl || 0);
        
        // Instant refresh for automation panel
        if (data.automation_stats) {
          const autoPnlEl = document.getElementById('autoPnl');
          if (autoPnlEl) {
            const autoPnl = data.automation_stats.pnl || 0;
            const sign = autoPnl > 0 ? '+' : (autoPnl < 0 ? '-' : '');
            autoPnlEl.textContent = `${sign}₹${Math.abs(autoPnl).toLocaleString('en-IN', {minimumFractionDigits: 2})}`;
            autoPnlEl.className = autoPnl > 0 ? 'pnl-positive' : (autoPnl < 0 ? 'pnl-negative' : '');
          }
          const autoTradesEl = document.getElementById('autoTrades');
          if (autoTradesEl) {
            autoTradesEl.textContent = `${data.automation_stats.trades}/2 Trades`;
          }
        }
        break;
      case 'orders':
        renderOrders(data.orders);
        break;
      case 'funds':
        updateFundsLive(data.funds);
        break;
      case 'log':
        appendActivityLog(data.msg, data.level, data.time);
        break;
      case 'strike_update':
        // Lightweight strike LTP update
        if (!analysisData) analysisData = { signals: [], strike_recommendations: [] };
        
        // Update the recommendations
        const currentTopSignal = analysisData.signals?.[0];
        if (currentTopSignal) {
          // If we have a signal, show the relevant strike (CALL or PUT)
          analysisData.strike_recommendations = currentTopSignal.type === 'CALL' ? data.ce_strikes : data.pe_strikes;
        } else {
          // If no signal, show both ATM CALL and Premium Matched PUT for monitoring
          analysisData.strike_recommendations = [...(data.ce_strikes || []), ...(data.pe_strikes || [])];
        }
        
        analysisData.expiry = data.expiry;
        
        // Re-render
        renderSignals(analysisData.signals);
        renderStrikes(analysisData.strike_recommendations);
        break;
      case 'market_update':
        // Update header with the activeSymbol
        let displayData = data.spots[activeSymbol];
        
        if (!displayData && activeSymbol === "NSE:NIFTY50-INDEX") {
            // Fallback if Nifty is missing but active
            const firstSym = Object.keys(data.spots)[0];
            if (firstSym) displayData = data.spots[firstSym];
        }

        if (displayData) {
            updateSpotLive({
                symbol: activeSymbol,
                spot: displayData.lp,
                change: displayData.change,
                change_pct: displayData.change_pct,
                vix: data.vix.lp,
                vix_change: data.vix.change
            });
        }
        
        // Refresh the Market Watch table
        renderScriptsList(data.spots);
        break;
    }
  };

  wsConnection.onclose = () => {
    console.log('📡 WebSocket disconnected. Reconnecting in 3s...');
    document.getElementById('statusBadge').innerHTML = `<span class="status-dot" style="background:#f0883e"></span>Fyers Connecting`;
    setTimeout(connectWebSocket, 3000);
  };

  wsConnection.onerror = (err) => {
    console.error('📡 WebSocket error:', err);
    wsConnection.close();
  };
}

function updateSpotLive(data) {
  if (!data) return;
  const spot = data.spot || data.lp || 0;
  const change = data.change || 0;
  const change_pct = data.change_pct || data.chp || 0;
  const vix = data.vix || 0;
  const vix_change = data.vix_change || 0;
  const symbol = data.symbol?.replace('NSE:', '').replace('-INDEX', '').replace('-EQ', '') || 'NIFTY';

  const spotLabel = document.getElementById('spotLabel');
  if (spotLabel) {
      spotLabel.textContent = symbol;
  }

  document.getElementById('spotPrice').textContent = '₹' + Number(spot).toLocaleString('en-IN', { minimumFractionDigits: 2 });

  const changeEl = document.getElementById('spotChange');
  const sign = change >= 0 ? '+' : '';
  changeEl.textContent = `${sign}${change.toFixed(2)} (${sign}${change_pct.toFixed(2)}%)`;
  changeEl.className = 'spot-change ' + (change >= 0 ? 'positive' : 'negative');

  document.getElementById('vixValue').textContent = Number(vix).toFixed(2);
  const vixChEl = document.getElementById('vixChange');
  const vSign = vix_change >= 0 ? '+' : '';
  vixChEl.textContent = `${vSign}${Number(vix_change).toFixed(2)}%`;
  vixChEl.className = 'spot-change ' + (vix_change >= 0 ? 'negative' : 'positive');

  // Update live spot lines on chart
  const spotPrice = Number(spot);

  if (series5m && spotPrice > 0) {
    if (!liveSpotLine5m) {
      liveSpotLine5m = series5m.createPriceLine({
        price: spotPrice,
        color: '#3b82f6',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'LIVE',
      });
    } else {
      liveSpotLine5m.applyOptions({ price: spotPrice });
    }
  }
}

function updateFundsLive(funds, pnl) {
  if (funds) {
    const available = funds.equityAmount !== undefined ? funds.equityAmount : funds.availableBalance;
    const used = funds.utilisedAmount !== undefined ? funds.utilisedAmount : (funds.usedAmount || 0);
    
    document.getElementById('fundAvailable').textContent = '₹' + Number(available || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    document.getElementById('fundUsed').textContent = '₹' + Number(used || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
  }
  if (pnl !== undefined) {
    updatePnlDisplays(pnl);
  }
}

function updatePnlDisplays(pnl) {
  const pnlEl = document.getElementById('totalPnl');
  const headerPnlEl = document.getElementById('headerPnl');
  
  const today = new Date().toDateString();
  const cachedData = JSON.parse(localStorage.getItem('dailyPnl') || '{}');
  
  let persistentPnl = pnl;
  
  // If API returns 0 but we have a cached PnL from today, use the cache
  if (pnl === 0 && cachedData.date === today && cachedData.pnl !== 0) {
    persistentPnl = cachedData.pnl;
  } else if (pnl !== 0) {
    // Update cache with fresh non-zero PnL
    localStorage.setItem('dailyPnl', JSON.stringify({ date: today, pnl: pnl }));
  }
  
  const sign = persistentPnl > 0 ? '+' : (persistentPnl < 0 ? '-' : '');
  const formatted = sign + '₹' + Math.abs(persistentPnl).toFixed(2);
  const colorClass = persistentPnl > 0 ? 'pnl-positive' : (persistentPnl < 0 ? 'pnl-negative' : '');
  
  if (pnlEl) {
    pnlEl.textContent = formatted;
    pnlEl.className = 'fund-value ' + colorClass;
    pnlEl.style.color = ''; 
  }
  if (headerPnlEl) {
    headerPnlEl.textContent = `P&L: ${formatted}`;
    headerPnlEl.className = colorClass;
    headerPnlEl.style.color = '';
  }
}


// ===== DATA FETCHING (for heavy/initial loads) =====
async function refreshAll() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.textContent = '⟳ Loading...';

  try {
    await fetchAnalysis();
    showToast('Data refreshed', 'success');
  } catch (e) {
    showToast('Refresh failed: ' + e.message, 'error');
  }

  btn.disabled = false;
  btn.textContent = '⟳ Refresh';
}

async function fetchSpot() {
  // Kept for manual refresh fallback
  const resp = await fetch('/api/spot');
  const data = await resp.json();
  updateSpotLive(data);
}

async function fetchCandles() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.textContent = '...';

  try {
    const resp5m = await fetch(`/api/candles?symbol=${activeSymbol}&resolution=5&days=3`);
    const data5m = await resp5m.json();

    // Convert to Lightweight Charts format (add IST offset)
    const format = (candles) => candles.map(c => ({
      time: c.timestamp + IST_OFFSET,
      open: c.open, high: c.high, low: c.low, close: c.close,
    }));

    const formatted5m = format(data5m.candles || []);
    
    // Only update chart if we received valid candle data (prevents wipe on API glitch)
    if (formatted5m.length > 0) {
      series5m.setData(formatted5m);

      // Track last 5m candle time for extending OB/FVG zones
      lastCandleTime5m = formatted5m[formatted5m.length - 1].time;
    }

    chart5m.timeScale().fitContent();
  } catch (e) {
    showToast('Refresh failed: ' + e.message, 'error');
  }

  btn.disabled = false;
  btn.textContent = '⟳ Refresh';
}

function handleVisibilityChange() {
  if (!analysisData || !chart5m || !series5m) return;
  
  try {
    renderKeyLevels(analysisData.key_levels);
    renderBOSOnChart(analysisData.bos_events);
    renderOrderBlocksOnChart(analysisData.order_blocks);
    renderFVGsOnChart(analysisData.fvgs);
    updateAllMarkers();

    // Save preferences
    localStorage.setItem('vis_OB', document.getElementById('toggleOB').checked);
    localStorage.setItem('vis_FVG', document.getElementById('toggleFVG').checked);
    localStorage.setItem('vis_KEY', document.getElementById('toggleKEY').checked);
  } catch (err) {
    console.error("Chart Visibility Update Error:", err);
  }
}

// Add to DOMContentLoaded to load preferences
function loadVisibilityPrefs() {
  const ob = localStorage.getItem('vis_OB');
  const fvg = localStorage.getItem('vis_FVG');
  const key = localStorage.getItem('vis_KEY');
  
  if (ob !== null) document.getElementById('toggleOB').checked = ob === 'true';
  if (fvg !== null) document.getElementById('toggleFVG').checked = fvg === 'true';
  // Default KEY to checked if no preference saved (key levels should always be visible)
  if (key !== null) {
    document.getElementById('toggleKEY').checked = key === 'true';
  } else {
    document.getElementById('toggleKEY').checked = true;
  }
}

async function fetchAnalysis() {
  try {
    const resp = await fetch(`/api/analysis?symbol=${activeSymbol}`);
    if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        console.warn('Analysis fetch partially failed:', errorData.detail || 'Rate Limited');
        return;
    }
    
    analysisData = await resp.json();
    if (!analysisData) return;

    // Update candles from analysis data if available
    if (analysisData.candles_5m && analysisData.candles_5m.length > 0) {
      const formatted = analysisData.candles_5m.map(c => ({
        time: c.timestamp + IST_OFFSET,
        open: c.open, high: c.high, low: c.low, close: c.close,
      }));
      series5m.setData(formatted);
      lastCandleTime5m = formatted[formatted.length - 1].time;
      chart5m.timeScale().fitContent();
    }

    if (analysisData.trend) renderTrend(analysisData.trend);
    
    // Render everything - each function now respects its own toggle
    renderKeyLevels(analysisData.key_levels || []);
    renderBOSOnChart(analysisData.bos_events || []);
    renderOrderBlocksOnChart(analysisData.order_blocks || []);
    renderFVGsOnChart(analysisData.fvgs || []);
    updateAllMarkers();

    renderSignals(analysisData.signals || []);
    renderStrikes(analysisData.strike_recommendations || []);

    const obCount = (analysisData.active_order_blocks || []).length;
    document.getElementById('obCount').textContent = `${obCount} OB`;
  } catch (e) {
    console.error('fetchAnalysis failed:', e);
  }
}

async function fetchPositions() {
  try {
    const [posResp, ordResp] = await Promise.all([
      fetch('/api/positions'), fetch('/api/orders'),
    ]);
    const posData = await posResp.json();
    const ordData = await ordResp.json();

    // Fyers returns netPositions list
    const positions = posData.netPositions || [];
    renderPositions(positions);
    
    // Also update PnL from overall summary if available
    if (posData.overallPnl !== undefined) {
       updatePnlDisplays(posData.overallPnl);
    }
    
    renderOrders(ordData.orders || []);
  } catch (e) {
    console.error('Position fetch error:', e);
  }
}

async function fetchFunds() {
  try {
    const [fundResp, posResp] = await Promise.all([
      fetch('/api/funds'),
      fetch('/api/positions')
    ]);
    const fundData = await fundResp.json();
    const posData = await posResp.json();
    
    // overallPnl is the correct field from Fyers API
    const totalPnl = posData.overallPnl !== undefined ? posData.overallPnl : 0;
    updateFundsLive(fundData.funds || {}, totalPnl);
  } catch (e) {
    console.error('Funds/P&L fetch error:', e);
  }
}


// ===== RENDERING =====
function renderTrend(trend) {
  const badge = document.getElementById('trendBadge');
  if (!trend || !trend.trend) {
    badge.textContent = 'NEUTRAL';
    badge.className = 'card-badge badge-neutral';
    return;
  }
  badge.textContent = `${trend.trend} (${trend.strength}%)`;
  badge.className = 'card-badge ' + (
    trend.trend === 'BULLISH' ? 'badge-bullish' :
    trend.trend === 'BEARISH' ? 'badge-bearish' : 'badge-neutral'
  );
}

function renderKeyLevels(levels) {
  clearKeyLevelLines();
  const container = document.getElementById('keyLevelsContainer');
  const showOnChart = document.getElementById('toggleKEY').checked;

  if (!levels || !levels.length) {
    container.innerHTML = '<div class="no-data">No key levels detected</div>';
    document.getElementById('levelCount').textContent = '0';
    return;
  }

  document.getElementById('levelCount').textContent = levels.length;

  // Always render the panel list (key levels data is always useful)
  container.innerHTML = levels.map(l => {
    const color = l.type === 'resistance' ? 'var(--bearish)' :
                  l.type === 'support' ? 'var(--bullish)' :
                  l.type === 'pivot' ? 'var(--accent-purple)' : 'var(--accent-yellow)';

    // Only add price lines to chart if KEY toggle is checked
    if (showOnChart) {
      try {
        const line = series5m.createPriceLine({
          price: l.price,
          color: l.type === 'resistance' ? '#ef4444' : l.type === 'support' ? '#10b981' : '#8b5cf6',
          lineWidth: 2,
          lineStyle: 1, // Solid line for better visibility
          axisLabelVisible: true,
          title: l.label || l.type,
        });
        markers1h.push(line);
      } catch (e) {
        console.error('Key level price line error:', e);
      }
    }

    return `<div class="level-item">
      <div>
        <span class="level-type ${l.type}">${l.type}</span>
        <span style="margin-left:6px;font-size:10px;color:var(--text-muted)">${l.label || ''}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="level-price">${l.price.toLocaleString('en-IN')}</span>
        <div class="strength-bar">
          <div class="strength-fill" style="width:${l.strength*10}%;background:${color}"></div>
        </div>
      </div>
    </div>`;
  }).join('');
}

let bosSeriesList = []; // BOS Ray series

function clearKeyLevelLines() {
  markers1h.forEach(m => { try { series5m.removePriceLine(m); } catch(e) {} });
  markers1h = [];
}

function clearBOS() {
  bosSeriesList.forEach(s => { try { chart5m.removeSeries(s); } catch(e) {} });
  bosSeriesList = [];
  window.bosMarkers = [];
}

function renderBOSOnChart(bosEvents) {
  clearBOS();
  if (!document.getElementById('toggleKEY').checked) return;
  window.bosMarkers = [];
  if (!bosEvents || !bosEvents.length) return;

  const endTime = lastCandleTime5m || Math.floor(Date.now() / 1000) + IST_OFFSET;

  bosEvents.forEach(bos => {
    const isBull = bos.type === 'BULLISH_BOS';
    const color = isBull ? '#3b82f6' : '#ef4444';

    const raySeries = chart5m.addLineSeries({
      color: color,
      lineWidth: 2,
      lineStyle: 2, // Dashed
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    raySeries.setData([
      { time: bos.timestamp + IST_OFFSET, value: bos.price },
      { time: endTime, value: bos.price }
    ]);
    bosSeriesList.push(raySeries);
    
    // Add text marker
    window.bosMarkers = window.bosMarkers || [];
    window.bosMarkers.push({
      time: bos.timestamp + IST_OFFSET,
      position: isBull ? 'belowBar' : 'aboveBar',
      color: color,
      shape: 'circle',
      text: 'BOS',
    });
  });
}

function clearOBZones() {
  obZoneSeries.forEach(s => { try { chart5m.removeSeries(s); } catch(e) {} });
  obZoneSeries = [];
  window.obMarkers = [];
}

function clearFVGZones() {
  fvgZoneSeries.forEach(s => { try { chart5m.removeSeries(s); } catch(e) {} });
  fvgZoneSeries = [];
  window.fvgMarkers = [];
}

function renderOrderBlocksOnChart(obs) {
  clearOBZones();
  if (!document.getElementById('toggleOB').checked) return;
  if (!obs || !obs.length) return;

  // Show only active OBs by default to reduce clutter
  const displayOBs = obs.filter(ob => ob.active);
  const endTime = lastCandleTime5m || Math.floor(Date.now() / 1000) + IST_OFFSET;

  displayOBs.forEach(ob => {
    const startTime = ob.timestamp + IST_OFFSET;
    const isBull = ob.direction === 'BULLISH';
    const topPrice = ob.top;
    const bottomPrice = ob.bottom;
    
    // Green for Bullish, Red for Bearish
    const color = isBull ? 'rgba(34, 197, 94, ' : 'rgba(239, 68, 68, ';
    const fillAlpha = '0.15';
    const lineAlpha = '0.4';
    const obFill = color + fillAlpha + ')';
    const obLine = color + lineAlpha + ')';

    try {
      // Create baseline series for the bounded zone rectangle
      const zoneSeries = chart5m.addBaselineSeries({
        baseValue: { type: 'price', price: bottomPrice },
        topFillColor1: obFill,
        topFillColor2: obFill,
        topLineColor: obLine,
        bottomFillColor1: 'transparent',
        bottomFillColor2: 'transparent',
        bottomLineColor: 'transparent',
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });

      // Fill from startTime to endTime at top price
      const numPoints = 6;
      const step = Math.max(1, Math.floor((endTime - startTime) / numPoints));
      const data = [];
      for (let t = startTime; t <= endTime; t += step) {
        data.push({ time: t, value: topPrice });
      }
      // Ensure end point
      if (data.length === 0 || data[data.length - 1].time < endTime) {
        data.push({ time: endTime, value: topPrice });
      }

      zoneSeries.setData(data);
      obZoneSeries.push(zoneSeries);

      // Also add a bottom border line series
      const borderSeries = chart5m.addLineSeries({
        color: obLine,
        lineWidth: 1,
        lineStyle: 2,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      borderSeries.setData([
        { time: startTime, value: bottomPrice },
        { time: endTime, value: bottomPrice },
      ]);
      obZoneSeries.push(borderSeries);
    } catch (e) {
      console.error('OB zone error:', e);
    }
  });

  // Add text markers on the candle series for OB
  const chartMarkers = displayOBs.map(ob => ({
    time: ob.timestamp + IST_OFFSET,
    position: ob.direction === 'BULLISH' ? 'belowBar' : 'aboveBar',
    color: ob.direction === 'BULLISH' ? '#22c55e' : '#ef4444',
    shape: ob.direction === 'BULLISH' ? 'arrowUp' : 'arrowDown',
    text: 'OB',
  }));
  chartMarkers.sort((a, b) => a.time - b.time);
  
  // Note: Since we also want FVG markers, we should combine them or set them together.
  // We'll manage all markers in a separate pass or just update the OB ones here for now.
  // Wait, if we call series5m.setMarkers() twice, the second overwrites the first.
  // We need to store global markers and set them once, or retrieve existing.
  window.obMarkers = chartMarkers;
}

function renderFVGsOnChart(fvgs) {
  clearFVGZones();
  if (!document.getElementById('toggleFVG').checked) return;
  if (!fvgs || !fvgs.length) return;

  const endTime = lastCandleTime5m || Math.floor(Date.now() / 1000) + IST_OFFSET;

  fvgs.filter(fvg => fvg.active).forEach(fvg => {
    const startTime = fvg.timestamp + IST_OFFSET;
    const isBull = fvg.direction === 'BULLISH';
    const topPrice = fvg.top;
    const bottomPrice = fvg.bottom;

    try {
      const zoneSeries = chart5m.addBaselineSeries({
        baseValue: { type: 'price', price: bottomPrice },
        topFillColor1: isBull ? 'rgba(245, 158, 11, 0.15)' : 'rgba(139, 92, 246, 0.15)',
        topFillColor2: isBull ? 'rgba(245, 158, 11, 0.15)' : 'rgba(139, 92, 246, 0.15)',
        topLineColor: isBull ? 'rgba(245, 158, 11, 0.5)' : 'rgba(139, 92, 246, 0.5)',
        bottomFillColor1: 'transparent',
        bottomFillColor2: 'transparent',
        bottomLineColor: 'transparent',
        lineWidth: 1,
        lineStyle: 2,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });

      const numPoints = 6;
      const step = Math.max(1, Math.floor((endTime - startTime) / numPoints));
      const data = [];
      for (let t = startTime; t <= endTime; t += step) {
        data.push({ time: t, value: topPrice });
      }
      if (data.length === 0 || data[data.length - 1].time < endTime) {
        data.push({ time: endTime, value: topPrice });
      }

      zoneSeries.setData(data);
      fvgZoneSeries.push(zoneSeries);

      // Bottom border
      const borderSeries = chart5m.addLineSeries({
        color: isBull ? 'rgba(245, 158, 11, 0.4)' : 'rgba(139, 92, 246, 0.4)',
        lineWidth: 1,
        lineStyle: 3,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      borderSeries.setData([
        { time: startTime, value: bottomPrice },
        { time: endTime, value: bottomPrice },
      ]);
      fvgZoneSeries.push(borderSeries);
    } catch (e) {
      console.error('FVG zone error:', e);
    }
  });

  // Create markers for FVGs
  window.fvgMarkers = fvgs.filter(fvg => fvg.active).map(fvg => ({
    time: fvg.timestamp + IST_OFFSET,
    position: fvg.direction === 'BULLISH' ? 'belowBar' : 'aboveBar',
    color: fvg.direction === 'BULLISH' ? '#f59e0b' : '#8b5cf6',
    shape: 'circle',
    text: 'FVG',
  }));
}

function updateAllMarkers() {
  const showOB = document.getElementById('toggleOB')?.checked;
  const showFVG = document.getElementById('toggleFVG')?.checked;
  const showKEY = document.getElementById('toggleKEY')?.checked;
  
  console.log(`📊 Updating Markers: OB=${showOB}, FVG=${showFVG}, KEY=${showKEY}`);
  
  const markerMap = new Map(); // Map time -> markers
  
  const process = (markers, type) => {
    if (!markers) return;
    markers.forEach(m => {
      if (!markerMap.has(m.time)) markerMap.set(m.time, []);
      markerMap.get(m.time).push({ ...m, type });
    });
  };
  
  if (showOB) process(window.obMarkers, 'OB');
  if (showFVG) process(window.fvgMarkers, 'FVG');
  
  const finalMarkers = [];
  markerMap.forEach((list, time) => {
    if (list.length === 1) {
      finalMarkers.push(list[0]);
    } else {
      // Merge overlapping markers
      const types = [...new Set(list.map(l => l.type))].sort();
      const merged = { ...list[0] };
      merged.text = types.join('+');
      finalMarkers.push(merged);
    }
  });
  
  // Add BOS and others that shouldn't be merged
  if (window.bosMarkers) finalMarkers.push(...window.bosMarkers);
  
  if (series5m) {
    series5m.setMarkers(finalMarkers.sort((a, b) => a.time - b.time));
  }
}

function renderSignals(signals) {
  const container = document.getElementById('signalsContainer');
  document.getElementById('signalCount').textContent = signals.length;

  if (!signals.length) {
    container.innerHTML = '<div class="no-signal"><div class="no-signal-icon">📡</div><div class="no-signal-text">No active signals — waiting for setup at key levels</div></div>';
    return;
  }

  // Get strike info if available
  const strikes = analysisData?.strike_recommendations || [];
  const bestStrike = strikes[0];

  container.innerHTML = signals.map((sig, i) => {
    const isCall = sig.type === 'CALL';
    const isAdvisory = sig.advisory_only === true;
    const confClass = sig.confidence >= 70 ? 'confidence-high' : sig.confidence >= 50 ? 'confidence-medium' : 'confidence-low';

    // Build strike + expiry info line
    let strikeInfo = '';
    const expiry = analysisData?.expiry;
    const expiryStr = expiry ? `${expiry.date} (${expiry.day}) · ${expiry.dte}DTE` : '';
    if (bestStrike && !isAdvisory) {
      strikeInfo = `<div style="font-size:11px;color:var(--accent-cyan);margin-bottom:4px;font-family:'JetBrains Mono',monospace">
        Strike: ${bestStrike.strike} ${isCall ? 'CE' : 'PE'} · LTP: ₹${bestStrike.ltp?.toFixed(2) || '--'}
      </div>
      <div style="font-size:10px;color:var(--accent-yellow);margin-bottom:4px">
        Expiry: ${expiryStr}
      </div>`;
    } else if (expiryStr) {
      strikeInfo = `<div style="font-size:10px;color:var(--accent-yellow);margin-bottom:4px">Expiry: ${expiryStr}</div>`;
    }

    // Prices: Buy, SL, Target (Showing NIFTY levels for clarity)
    let priceInfo = '';
    if (!isAdvisory) {
      // Get Option prices if bestStrike available
      const optionBuy = bestStrike ? (bestStrike.locked_price || bestStrike.ltp || 0) : 0;
      const optionSl = bestStrike ? (bestStrike.sl || optionBuy - 12.0) : 0;
      const optionTgt = bestStrike ? (bestStrike.target || optionBuy + 24.0) : 0;

      priceInfo = `
        <div class="signal-price-grid">
          <div class="signal-price-item">
            <div class="signal-price-label">NIFTY ENTRY</div>
            <div class="signal-price-value" style="color:var(--accent-cyan)">₹${sig.entry_price?.toFixed(1) || '--'}</div>
            ${bestStrike ? `<div style="font-size:9px;color:var(--text-muted)">Opt: ₹${optionBuy.toFixed(2)}</div>` : ''}
          </div>
          <div class="signal-price-item">
            <div class="signal-price-label">STOPLOSS</div>
            <div class="signal-price-value" style="color:var(--bearish)">₹${sig.sl?.toFixed(1) || '--'}</div>
            ${bestStrike ? `<div style="font-size:9px;color:var(--text-muted)">Opt: ₹${optionSl.toFixed(2)}</div>` : ''}
          </div>
          <div class="signal-price-item">
            <div class="signal-price-label">TARGET</div>
            <div class="signal-price-value" style="color:var(--bullish)">₹${sig.target?.toFixed(1) || '--'}</div>
            ${bestStrike ? `<div style="font-size:9px;color:var(--text-muted)">Opt: ₹${optionTgt.toFixed(2)}</div>` : ''}
          </div>
        </div>
      `;
    }

    const spotEl = document.getElementById('spotPrice');
    const currentSpot = spotEl ? spotEl.textContent.replace(/[₹,]/g, '') : '--';

    // Advisory: no BUY button, just info
    if (isAdvisory) {
      return `<div class="signal-card" style="opacity:0.7;border-color:var(--border)">
        <div class="signal-header">
          <span class="signal-type ${isCall ? 'call' : 'put'}">
            ${isCall ? '📈' : '📉'} ${sig.type} WATCH
          </span>
          <span class="confidence-badge" style="background:rgba(100,116,139,0.2);color:var(--text-muted)">WATCHING</span>
        </div>
        <div class="signal-reason">${sig.reason}</div>
        ${strikeInfo}
        ${priceInfo}
        <div class="signal-zone" style="margin-top:8px">Spot: ${currentSpot}</div>
      </div>`;
    }

    // AI Rationale Section
    const aiSection = sig.ai_rationale ? `
      <div class="ai-box" style="margin-top:12px; padding:10px; background:rgba(99,179,237,0.08); border:1px solid rgba(99,179,237,0.2); border-radius:8px; font-size:11px; animation: signal-slide-in 0.6s ease-out">
        <div style="color:var(--accent-blue); font-weight:700; display:flex; justify-content:space-between; align-items:center; margin-bottom:4px">
          <span>🤖 AI CONFIRMATION</span>
          <span style="background:var(--accent-blue); color:white; padding:1px 6px; border-radius:10px; font-size:9px">${sig.ai_confidence}%</span>
        </div>
        <div style="color:var(--text-secondary); line-height:1.4">"${sig.ai_rationale}"</div>
      </div>
    ` : '';

    // Actionable signal with BUY button
    return `<div class="signal-card ${isCall ? 'call' : 'put'} ${sig.confidence >= 80 ? 'high-conf' : ''}">
      <div class="signal-header">
        <span class="signal-type ${isCall ? 'call' : 'put'}">
          ${isCall ? '⬆️' : '⬇️'} ${sig.symbol?.replace('NSE:', '') || 'NIFTY'} ${sig.type} BUY
        </span>
        <span class="confidence-badge ${confClass}">${sig.confidence}%</span>
      </div>
      <div class="signal-reason" style="font-weight:600; color:var(--accent-blue); margin-bottom:4px">${sig.symbol?.replace('-INDEX', '').replace('-EQ', '') || ''}</div>
      <div class="signal-reason">${sig.reason}</div>
      ${strikeInfo}
      ${priceInfo}
      <div class="confidence-meter"><div class="confidence-fill ${sig.confidence >= 70 ? 'high' : sig.confidence >= 50 ? 'medium' : 'low'}" style="width:${sig.confidence}%"></div></div>
      <div class="signal-zone" style="margin-top:8px">Spot: ${currentSpot} · Zone: ${sig.entry_zone_bottom?.toFixed(2) || '--'} — ${sig.entry_zone_top?.toFixed(2) || '--'}</div>
      ${aiSection}
      <div class="signal-actions">
        <button class="btn-buy" onclick="openOrderFromSignal(${i})">🟢 BUY</button>
        <button class="btn-skip" onclick="skipSignal(${i})">❌ SKIP</button>
      </div>
    </div>`;
  }).join('');
}

function renderStrikes(strikes) {
  const container = document.getElementById('strikesContainer');
  const expiry = analysisData?.expiry;

  if (!strikes || !strikes.length) {
    container.innerHTML = '<div class="no-signal"><div class="no-signal-text">Waiting for signal...</div></div>';
    return;
  }

  // Expiry header
  const expiryHeader = expiry
    ? `<div style="padding:6px 12px;font-size:10px;color:var(--accent-yellow);border-bottom:1px solid var(--border);font-family:'JetBrains Mono',monospace">
        📅 Expiry: ${expiry.date} (${expiry.day}) · ${expiry.dte} DTE
       </div>`
    : '';

  container.innerHTML = expiryHeader + strikes.map((s, i) => `
    <div class="strike-item ${i === 0 ? 'selected' : ''}">
      <div class="strike-info">
        <span class="strike-value">${s.strike} ${s.type_label?.includes('CALL') ? 'CE' : 'PE'}</span>
        <span class="strike-label">${s.type_label} · ${s.moneyness || ''}</span>
      </div>
      <span class="strike-premium">₹${s.ltp?.toFixed(2) || '--'}</span>
    </div>
  `).join('');
}

let lastPositionsJSON = "";
function renderPositions(positions) {
  const currentJSON = JSON.stringify(positions);
  if (currentJSON === lastPositionsJSON) return;
  lastPositionsJSON = currentJSON;

  const container = document.getElementById('positionsContainer');
  const activePositions = positions ? positions.filter(p => p.qty !== 0) : [];
  
  // Calculate total PnL from ALL positions (including realized PnL from closed ones)
  let totalPnl = 0;
  if (positions) {
    positions.forEach(p => { totalPnl += (p.pl || 0); });
  }

  if (!activePositions.length) {
    container.innerHTML = '<div class="no-signal"><div class="no-signal-text">No open positions</div></div>';
  } else {
    container.innerHTML = activePositions.map(p => {
      const pnl = p.pl || 0;
      const side = p.side > 0 ? 'LONG' : 'SHORT';
      return `<div class="position-item">
        <div>
          <div style="font-weight:600;font-size:12px">${p.symbol}</div>
          <div style="font-size:10px;color:var(--text-muted)">${side} · Qty: ${Math.abs(p.qty)} · Avg: ₹${p.buyAvg?.toFixed(2) || p.sellAvg?.toFixed(2)}</div>
        </div>
        <div style="text-align:right">
          <div class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}" style="font-family:'JetBrains Mono';font-weight:600">
            ${pnl > 0 ? '+' : (pnl < 0 ? '-' : '')}₹${Math.abs(pnl).toFixed(2)}
          </div>
          <div style="font-size:10px;color:var(--text-muted)">LTP: ₹${p.ltp?.toFixed(2)}</div>
        </div>
      </div>`;
    }).join('');
  }

  updatePnlDisplays(totalPnl);
}

let lastOrdersJSON = "";
function renderOrders(orders) {
  const currentJSON = JSON.stringify(orders);
  if (currentJSON === lastOrdersJSON) return;
  lastOrdersJSON = currentJSON;

  const container = document.getElementById('ordersContainer');
  if (!orders || !orders.length) {
    container.innerHTML = '<div class="no-signal"><div class="no-signal-text">No orders today</div></div>';
    return;
  }

  container.innerHTML = orders.slice(-5).reverse().map(o => {
    const side = o.side > 0 ? 'BUY' : 'SELL';
    const statusColor = o.status === 2 ? 'var(--bullish)' : o.status === 1 ? 'var(--accent-yellow)' : 'var(--text-muted)';
    return `<div class="position-item">
      <div>
        <div style="font-weight:600;font-size:12px">${o.symbol}</div>
        <div style="font-size:10px;color:var(--text-muted)">${side} · Qty: ${o.qty}</div>
      </div>
      <div style="color:${statusColor};font-size:11px;font-weight:600">
        ${o.status === 2 ? 'FILLED' : o.status === 1 ? 'PENDING' : 'CANCELLED'}
      </div>
    </div>`;
  }).join('');
}


// ===== ORDER MANAGEMENT =====
function openOrderFromSignal(idx) {
  if (!analysisData || !analysisData.signals[idx]) return;
  const sig = analysisData.signals[idx];
  const strikes = analysisData.strike_recommendations || [];
  const bestStrike = strikes[0];

  if (!bestStrike) {
    showToast('No strike available for this signal', 'error');
    return;
  }

  document.getElementById('modalTitle').textContent = `${sig.type} BUY — ${bestStrike.strike} ${sig.type === 'CALL' ? 'CE' : 'PE'}`;
  document.getElementById('modalSymbol').value = bestStrike.symbol;
  document.getElementById('modalQty').value = 65;
  document.getElementById('modalSide').value = 'BUY';

  document.getElementById('orderModal').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('orderModal').classList.add('hidden');
}

async function runSystemCheck() {
  showToast('Running System Integrity Check...', 'info');
  try {
    const resp = await fetch('/api/test-signal');
    const result = await resp.json();
    if (result.success) {
      showToast('✅ Signal Logic Verified. Refreshing history...', 'success');
      setTimeout(() => {
        fetchSignalHistory();
        refreshAll();
      }, 1000);
    } else {
      showToast('❌ System Check Failed: ' + result.message, 'error');
    }
  } catch (err) {
    showToast('❌ Connection Error during check', 'error');
  }
}

async function confirmOrder() {
  const symbol = document.getElementById('modalSymbol').value;
  const qty = parseInt(document.getElementById('modalQty').value);
  const side = document.getElementById('modalSide').value;
  const orderType = document.getElementById('modalOrderType').value;
  const product = document.getElementById('modalProduct').value;
  const slPoints = parseFloat(document.getElementById('modalSLPoints').value) || 0;
  const targetPoints = parseFloat(document.getElementById('modalTargetPoints').value) || 0;

  closeModal();
  showToast(`Placing ${side} order for ${symbol}...`, 'info');

  try {
    const resp = await fetch('/api/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        symbol, qty, side, order_type: orderType, product,
        sl_points: slPoints, target_points: targetPoints
      }),
    });
    const result = await resp.json();

    if (result.success) {
      showToast(`✅ Order placed! ID: ${result.order_id}`, 'success');
      setTimeout(() => { fetchPositions(); fetchFunds(); }, 2000);
    } else {
      showToast(`❌ Order failed: ${result.message}`, 'error');
    }
  } catch (e) {
    showToast('❌ Order error: ' + e.message, 'error');
  }
}

async function skipSignal(idx) {
  const cards = document.querySelectorAll('.signal-card');
  const skipBtn = cards[idx] ? cards[idx].querySelector('.btn-skip') : null;
  const buyBtn = cards[idx] ? cards[idx].querySelector('.btn-buy') : null;

  if (cards[idx]) {
    cards[idx].style.opacity = '0.5';
    cards[idx].style.filter = 'grayscale(1)';
    cards[idx].style.pointerEvents = 'none';
  }
  if (skipBtn) skipBtn.disabled = true;
  if (buyBtn) buyBtn.disabled = true;

  showToast('Skipping signal...', 'info');
  
  try {
    const resp = await fetch('/api/skip-signal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: idx })
    });
    const result = await resp.json();
    if (result.success) {
      showToast('Signal skipped', 'success');
      
      // Immediately remove from local memory to prevent re-renders before next WS update
      if (analysisData && analysisData.signals && analysisData.signals[idx]) {
         analysisData.signals.splice(idx, 1);
         renderSignals(analysisData.signals);
         
         // Clear recommendations if no more signals
         if (analysisData.signals.length === 0) {
            analysisData.strike_recommendations = [];
            renderStrikes([]);
         }
      }
      
      // Refresh history panel
      fetchSignalHistory();
    } else {
      showToast('Failed to skip: ' + result.message, 'error');
      // Re-enable if failed
      if (cards[idx]) {
        cards[idx].style.opacity = '1';
        cards[idx].style.filter = 'none';
        cards[idx].style.pointerEvents = 'auto';
      }
      if (skipBtn) skipBtn.disabled = false;
      if (buyBtn) buyBtn.disabled = false;
    }
  } catch (e) {
    showToast('❌ Skip error: ' + e.message, 'error');
    if (cards[idx]) {
        cards[idx].style.opacity = '1';
        cards[idx].style.pointerEvents = 'auto';
    }
  }
}


// ===== TOAST =====
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ===== SIGNAL HISTORY =====
let historyMinimized = false;
function toggleCard(cardId, expandedMaxHeight) {
  const card = document.getElementById(cardId);
  if (!card) return;
  
  const body = card.querySelector('.card-body');
  const icon = document.getElementById(cardId.replace('Card', 'ToggleIcon'));
  const headerPnl = document.getElementById('headerPnl');
  
  if (body.style.display === 'none') {
    body.style.display = ''; // Restore original display
    card.style.maxHeight = expandedMaxHeight || '1000px';
    if (icon) icon.textContent = '▼';
    if (cardId === 'fundsCard' && headerPnl) headerPnl.style.display = 'none';
  } else {
    body.style.display = 'none';
    card.style.maxHeight = '44px';
    if (icon) icon.textContent = '▶';
    if (cardId === 'fundsCard' && headerPnl) headerPnl.style.display = 'inline-block';
  }
  
  // Special handling for chart resize
  if (cardId === 'masterChartCard' && typeof chart5m !== 'undefined') {
    setTimeout(() => {
      const el = document.getElementById('chart5m');
      if (el) chart5m.resize(el.clientWidth, el.clientHeight);
    }, 310);
  }
}

// Keep old names for compatibility if called from elsewhere, but point to new one
function toggleHistory() { toggleCard('historyCard', '220px'); }
function toggleKeyLevels() { toggleCard('keyLevelsCard', '200px'); }

async function fetchSignalHistory() {
  if (historyMinimized) return;
  try {
    const resp = await fetch('/api/signal-history');
    const data = await resp.json();
    if (data.success && data.history) {
      renderSignalHistory(data.history);
    }
  } catch (err) {
    console.error("Failed to fetch signal history", err);
  }
}

function renderSignalHistory(history) {
  const container = document.getElementById('historyContainer');
  if (!history || history.length === 0) {
    container.innerHTML = '<div class="no-signal"><div class="no-signal-text">No recent history...</div></div>';
    return;
  }
  
  let html = '';
  history.forEach(item => {
    let actionClass = 'badge-advisory';
    if (item.action.includes('TRADED')) actionClass = 'badge-traded';
    else if (item.action.includes('FAILED') || item.action.includes('ERROR')) actionClass = 'badge-failed';
    else if (item.action.includes('SKIPPED')) {
      if (item.action.includes('Auto Off') || item.action.includes('No Strikes')) actionClass = 'badge-skipped';
      else actionClass = 'badge-advisory';
    }
    
    // Extract HH:MM:SS from timestamp if possible
    let timeStr = item.time;
    if (timeStr.includes(' ')) {
      timeStr = timeStr.split(' ')[1];
    }
    
    let tradeHtml = '';
    if (item.trade) {
      const t = item.trade;
      tradeHtml = `
        <div class="history-trade">
          ${t.strike} @ ${t.entry?.toFixed(1) || '--'} | SL: ${t.sl?.toFixed(1) || '--'} | TGT: ${t.target?.toFixed(1) || '--'}
        </div>
      `;
    }
    
    html += `
      <div class="history-item">
        <div class="history-time">${timeStr}</div>
        <div class="history-main">
          <span class="history-type" style="color: ${item.type==='CALL' ? 'var(--bullish)' : 'var(--bearish)'}">${item.type} NIFTY</span>
          <span class="history-badge ${actionClass}">${item.action}</span>
        </div>
        ${tradeHtml}
      </div>
    `;
  });
  
  container.innerHTML = html;
}

/**
 * Appends a new message to the Activity Log section
 */
function appendActivityLog(msg, level = 'info', time = null) {
  const container = document.getElementById('activityLog');
  const statusEl = document.getElementById('logStatus');
  if (!container) return;

  const timestamp = time || new Date().toLocaleTimeString([], { hour12: false });
  
  const entry = document.createElement('div');
  entry.className = `log-entry log-${level}`;
  
  entry.innerHTML = `
    <span class="log-time">[${timestamp}]</span>
    <span class="log-msg">${msg}</span>
  `;
  
  container.appendChild(entry);
  
  // Auto-scroll to bottom
  container.scrollTop = container.scrollHeight;
  
  // Limit to 100 entries to prevent memory issues
  while (container.childNodes.length > 100) {
    container.removeChild(container.firstChild);
  }
  
  // Update status text if it's a heartbeat/success
  if (statusEl && (level === 'success' || msg.includes('refreshed'))) {
    statusEl.textContent = 'ONLINE';
    statusEl.style.color = 'var(--bullish)';
  } else if (statusEl && level === 'error') {
    statusEl.textContent = 'ERROR DETECTED';
    statusEl.style.color = 'var(--bearish)';
  }
}

// ===== BETA v3 ADDITIONS =====

function renderSymbolTabs() {
  const container = document.getElementById('symbolTabs');
  if (!container || !activeScripts || activeScripts.length === 0) return;
  
  container.innerHTML = activeScripts.map(s => {
    const label = s.replace('NSE:', '').replace('-INDEX', '').replace('-EQ', '');
    const isActive = s === activeSymbol;
    return `<div class="symbol-tab ${isActive ? 'active' : ''}" onclick="selectScript('${s}')">${label}</div>`;
  }).join('');
}

// Price flash animation on spot price change
let lastSpotPrice = 0;
const originalUpdateSpotLive = updateSpotLive;
updateSpotLive = function(data) {
  const spot = data.spot || data.lp || 0;
  const spotEl = document.getElementById('spotPrice');
  
  if (spotEl && lastSpotPrice > 0 && spot !== lastSpotPrice) {
    spotEl.classList.remove('price-flash-up', 'price-flash-down');
    void spotEl.offsetWidth; // Force reflow
    spotEl.classList.add(spot > lastSpotPrice ? 'price-flash-up' : 'price-flash-down');
  }
  lastSpotPrice = spot;
  
  originalUpdateSpotLive(data);
  
  // Update footer timestamp
  const footerUpdate = document.getElementById('footerLastUpdate');
  if (footerUpdate) {
    footerUpdate.textContent = 'Last: ' + new Date().toLocaleTimeString([], { hour12: false });
  }
};

async function fetchVersion() {
  try {
    const resp = await fetch('/api/version');
    const data = await resp.json();
    const badge = document.getElementById('versionBadge');
    const footer = document.getElementById('footerVersion');
    if (badge) badge.textContent = data.version || 'v3.0';
    if (footer) footer.textContent = `${data.name || 'Sritej Trading'} ${data.version || ''}`;
    
    const footerSymbols = document.getElementById('footerSymbols');
    if (footerSymbols) footerSymbols.textContent = `${data.active_symbols || 1} symbol${data.active_symbols !== 1 ? 's' : ''}`;
    
    const footerAI = document.getElementById('footerAI');
    if (footerAI) {
      footerAI.textContent = data.ai_active ? 'AI: Active' : 'AI: Offline';
      footerAI.style.color = data.ai_active ? 'var(--bullish)' : 'var(--text-muted)';
    }
  } catch (e) {
    console.log('Version fetch failed:', e);
  }
}
