const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const fmt = (n, d = 1) => n === null || n === undefined || isNaN(n) ? '—'
  : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmt0 = n => n === null || n === undefined || isNaN(n) ? '—' : Number(n).toLocaleString('en-IN');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = { sort: 'score', order: 'desc', verdict: '', poll: null };

const PILLARS = [
  ['promoter', 'Promoter conviction', 18, 'Owning the book, and whether that stake is rising or leaking away.'],
  ['balance_sheet', 'Balance sheet', 18, 'Debt against net worth, interest cover, and the direction of borrowings.'],
  ['profitability', 'Profitability', 20, 'ROCE, margin trend, and whether receivables are quietly stretching.'],
  ['growth', 'Growth', 17, 'Five-year sales and profit CAGR, plus recent acceleration.'],
  ['cash', 'Cash generation', 12, 'Free cash flow and how much of reported profit actually arrives as cash.'],
  ['valuation', 'Valuation', 15, 'Earnings yield against the G-Sec, PEG, and the gap to modelled fair value.'],
];

const GATES = [
  'Promoter holding below the gate, or not published at all.',
  'Any pledged promoter shares.',
  'Loss-making on a trailing-twelve-month basis.',
  'Interest coverage under 1.5x while carrying debt.',
  'Debt / equity above 2x.',
  'Promoters selling out of more than 3% in a year.',
];

function renderMethod() {
  $('#pillars').innerHTML = PILLARS.map(([k, name, w, blurb]) =>
    `<div class="pcard"><b>${name}<i>${w}%</i></b><p>${blurb}</p></div>`).join('');
  $('#gates').innerHTML = GATES.map(g => `<li>${g}</li>`).join('');
}

function params() {
  const p = new URLSearchParams({
    min_promoter: $('#minPromoter').value,
    min_score: $('#minScore').value,
    min_mcap: $('#minMcap').value,
    max_pe: $('#maxPe').value,
    q: $('#q').value.trim(),
    sort: state.sort,
    order: state.order,
    limit: 250,
  });
  if (state.verdict) p.set('verdict', state.verdict);
  return p;
}

function verdictClass(v) {
  return { 'STRONG BUY': 'strong', 'BUY': 'buy', 'HOLD': 'hold', 'AVOID': 'avoid' }[v] || 'hold';
}

function gapCell(r) {
  const fv = r.fair_value || {};
  if (!fv.available || fv.gap_to_mid_pct === null || fv.gap_to_mid_pct === undefined) return '<span class="dim">—</span>';
  const g = fv.gap_to_mid_pct;
  return `<span class="${g >= 0 ? 'pos' : 'neg'}">${g >= 0 ? '+' : ''}${fmt0(g)}%</span>`;
}

function rowHTML(r) {
  const m = r.metrics;
  return `<tr data-key="${r.key}">
    <td><div class="co"><b>${r.name || r.key}</b><small>${r.key} · ${r.track === 'financial' ? 'financial' : 'corporate'}</small></div></td>
    <td class="num">${fmt(m.promoter_pct)}</td>
    <td class="num"><div class="scorecell"><span class="sbar"><i style="width:${Math.max(0, Math.min(100, r.score))}%"></i></span>${fmt(r.score)}</div></td>
    <td class="num">${fmt0(m.market_cap)}</td>
    <td class="num">${fmt(m.pe, 1)}</td>
    <td class="num">${fmt(m.return_ratio, 1)}</td>
    <td class="num">${gapCell(r)}</td>
    <td><span class="tag ${verdictClass(r.verdict)}">${r.verdict}</span></td>
  </tr>`;
}

async function loadResults() {
  const res = await fetch('/api/results?' + params());
  const data = await res.json();
  const rows = data.rows || [];
  $('#rows').innerHTML = rows.map(rowHTML).join('');
  $('#empty').hidden = data.total_matched > 0;
  $$('#results tbody tr').forEach(tr => tr.onclick = () => openDrawer(tr.dataset.key));
  const shown = Math.min(rows.length, data.total_matched);
  $('#tableFoot').textContent = `${data.total_matched} companies match the screen — showing top ${shown}. `
    + `Click any row for the full balance-sheet read and the reasoning behind the verdict.`;
}

async function loadStatus() {
  const s = await (await fetch('/api/status')).json();
  const c = s.counts || {};
  $('#coverageLine').textContent = c.total
    ? `${fmt0(c.parsed)} of ${fmt0(c.total)} listed companies read · ${fmt0(c.failed)} failed · ${fmt0(c.pending)} queued`
    : 'Universe not indexed yet — start a scrape to index all of screener.in.';
  const pct = s.target ? Math.round(((s.done + s.failed) / s.target) * 100) : 0;
  $('#barFill').style.width = (s.running ? Math.max(pct, 2) : (c.total ? 100 : 0)) + '%';
  if (s.running) {
    const eta = s.eta_s ? ` · ETA ${Math.round(s.eta_s / 60)} min` : '';
    $('#progMeta').textContent = `Scraping ${s.done}/${s.target}${eta} · polite spacing ${s.http.spacing_s}s · `
      + `${s.http.throttled} rate-limit backoffs · last: ${s.last_key || '—'}`;
  } else if (c.parsed) {
    $('#progMeta').textContent = `Idle. ${fmt0(c.parsed)} companies cached — re-running only fetches what is stale.`;
  } else {
    $('#progMeta').textContent = 'Idle. Nothing has been scraped yet.';
  }
  $('#btnScan').disabled = !!s.running;
  $('#btnStop').disabled = !s.running;
  return s;
}

async function scrape() {
  await fetch('/api/scrape', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  startPolling();
}

function startPolling() {
  if (state.poll) return;
  state.poll = setInterval(async () => {
    const s = await loadStatus();
    if (!s.running) {
      clearInterval(state.poll);
      state.poll = null;
      await loadResults();
      await loadStatus();
    }
  }, 2500);
}

/* ---------- detail drawer ---------- */

function stat(label, value) {
  return `<div class="d-stat"><span>${label}</span><b>${value}</b></div>`;
}

function fvBlock(fv, price) {
  if (!fv.available) return `<p class="sub">${fv.method || 'Not valueable on earnings.'}</p>`;
  const span = fv.high - fv.low || 1;
  const pos = Math.max(0, Math.min(100, ((price - fv.low) / span) * 100));
  const pb = (fv.build && fv.build.kind) === 'price_to_book' || fv.basis === 'price-to-book';
  const anchor = pb
    ? `${fv.justified_pe}x book value at ${fv.required_return_pct}% cost of equity`
    : `${fv.justified_pe}x on ${fv.build ? fv.build.eps_basis : 'trailing EPS'} at ${fv.required_return_pct}% required return`;
  return `<div class="fv"><div style="flex:1">
      <div class="fv-track"><div class="fv-mark" style="left:${pos}%"></div></div>
      <div class="fv-labels"><span>₹${fmt0(fv.low)}</span><span>fair ₹${fmt0(fv.mid)}</span><span>₹${fmt0(fv.high)}</span></div>
    </div></div>
    <p class="sub" style="margin-top:10px">CMP ₹${fmt0(price)} sits ${fv.gap_to_mid_pct >= 0
      ? `<b class="pos">${fmt0(fv.gap_to_mid_pct)}% below</b>` : `<b class="neg">${fmt0(Math.abs(fv.gap_to_mid_pct))}% above</b>`}
      the modelled anchor (${anchor}). ${fv.method}</p>`;
}

function peBlock(m, fv) {
  const b = fv && fv.build;
  const ladder = (m.eps_ladder || []).slice(-6);
  if (!b && !ladder.length) return '<p class="sub">No earnings history to build a multiple on.</p>';
  let out = '';
  if (b && b.kind === 'required_return') {
    out += `<table class="mini-table"><thead><tr><th>Hurdle rate</th><th>pts</th></tr></thead><tbody>`
      + b.rows.map(r => `<tr><td>${esc(r.label)}</td><td class="${r.points > 0 ? 'pos' : r.points < 0 ? 'neg' : ''}">`
        + `${r.points > 0 ? '+' : ''}${fmt(r.points, 2)}</td></tr>`).join('')
      + `<tr class="tot"><td>Required return</td><td>${fmt(b.total_pct, 2)}%</td></tr>`
      + (b.floor_applied
        ? `<tr class="tot"><td>Floor applied — no business is bond-safe</td><td>${fmt(b.required_return_pct, 2)}%</td></tr>` : '')
      + `<tr class="tot"><td>Justified P/E</td><td>${fmt(b.justified_pe, 1)}x</td></tr>`
      + `<tr class="tot"><td>Current P/E</td><td>${b.current_pe ? fmt(b.current_pe, 1) + 'x' : '—'}</td></tr>`
      + `</tbody></table><p class="sub">${esc(b.note)}</p>`;
  } else if (b && b.kind === 'price_to_book') {
    out += `<table class="mini-table"><tbody>`
      + b.rows.map(r => `<tr><td>${esc(r.label)}</td><td>${esc(r.value)}</td></tr>`).join('')
      + `</tbody></table><p class="sub">${esc(b.note)}</p>`;
  }
  if (ladder.length) {
    out += `<table class="mini-table" style="margin-top:14px">
      <thead><tr><th>₹</th>${ladder.map(x => `<th>${esc(String(x.period).replace('Mar ', ''))}</th>`).join('')}</tr></thead>
      <tbody>
        <tr><td>EPS</td>${ladder.map(x => `<td>${fmt(x.eps, 2)}</td>`).join('')}</tr>
        <tr><td>P/E at CMP</td>${ladder.map(x => `<td>${fmt(x.pe_at_price, 1)}x</td>`).join('')}</tr>
      </tbody></table>
      <p class="sub">Today's price measured against each year's earnings. A row that falls
      through the years means profit grew faster than the share price; a row that rises means
      the multiple, not the business, is doing the work.</p>`;
  }
  return out;
}

function miniTable(periods, rows, labels) {
  const cols = periods.slice(-6);
  const idx = cols.map(c => periods.indexOf(c));
  const head = `<thead><tr><th>₹ Cr</th>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>`;
  const body = labels.map(([key, label]) => {
    const series = rows[key] || [];
    return `<tr><td>${label}</td>${idx.map(i => `<td>${series[i] === null || series[i] === undefined ? '—' : fmt0(series[i])}</td>`).join('')}</tr>`;
  }).join('');
  return `<table class="mini-table">${head}<tbody>${body}</tbody></table>`;
}

function shpBlock(sh) {
  const cols = (sh.periods || []).slice(-6);
  const idx = cols.map(c => (sh.periods || []).indexOf(c));
  const pick = s => cols.map((c, i) => `<td>${s[idx[i]] != null ? fmt(s[idx[i]], 2) : '—'}</td>`).join('');
  return `<table class="mini-table"><thead><tr><th>%</th>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
    <tbody>
      <tr><td>Promoters</td>${pick(sh.promoters || [])}</tr>
      <tr><td>FIIs</td>${pick(sh.fiis || [])}</tr>
      <tr><td>DIIs</td>${pick(sh.diis || [])}</tr>
      <tr><td>Public</td>${pick(sh.public || [])}</tr>
    </tbody></table>`;
}

async function openDrawer(key) {
  const res = await fetch('/api/company/' + encodeURIComponent(key));
  if (!res.ok) return;
  const { record: r, analysis: a } = await res.json();
  const m = a.metrics;
  const t = r.tables || {};
  const pl = t.profit_loss || {}, bs = t.balance_sheet || {}, cf = t.cash_flow || {};

  $('#drawerBody').innerHTML = `
    <div class="d-head">
      <h3>${r.name || key}</h3>
      <div class="sym">${key} · ${r.from_cache ? 'from cache' : 'live read'}</div>
    </div>
    <div class="d-verdict">
      <div class="d-score">${fmt(a.score, 0)}</div>
      <div>
        <span class="tag ${verdictClass(a.verdict)}">${a.verdict}</span>
        <div class="d-note">Confidence ${a.confidence}% — ${a.confidence < 84 ? 'some pillars had missing data and were re-weighted.' : 'all six pillars had data.'}</div>
      </div>
    </div>

    <div class="d-grid">
      ${stat('Promoter %', fmt(m.promoter_pct))}
      ${stat('1-yr change', m.promoter_chg_1y === null ? '—' : fmt(m.promoter_chg_1y) + '%')}
      ${stat('Market cap', '₹' + fmt0(m.market_cap) + ' Cr')}
      ${stat('Price', '₹' + fmt0(m.price))}
      ${stat('P/E', fmt(m.pe))}
      ${stat('PEG', fmt(m.peg, 2))}
      ${stat(r.track === 'financial' ? 'ROE (3y avg)' : 'ROCE', fmt(m.return_ratio, 1) + '%')}
      ${stat('OPM', fmt(m.opm, 1) + '%')}
${r.track === 'financial'
      ? stat('Equity / assets', fmt(m.equity_to_assets, 1) + '%') +
        stat('Price / book', fmt(m.price_to_book, 2) + 'x') +
        stat('Deposits', '₹' + fmt0(m.deposits) + ' Cr')
      : stat('Debt/Equity', fmt(m.debt_equity, 2)) +
        stat('Int. cover', fmt(m.interest_coverage, 1) + 'x') +
        stat('FCF', '₹' + fmt0(m.fcf) + ' Cr')}
      ${stat('Div yield', fmt(m.dividend_yield, 2) + '%')}
    </div>

    <div class="d-sec"><h4>Modelled value range</h4>${fvBlock(a.fair_value, m.price)}</div>
    <div class="d-sec"><h4>${a.fair_value.basis === 'price-to-book' ? 'P/B and fair value breakdown' : 'P/E and fair value breakdown'}</h4>${peBlock(m, a.fair_value)}</div>

    <div class="d-sec"><h4>Pillars</h4><div class="pill-row">
      ${Object.entries(a.pillars).map(([k, v]) => {
        const meta = PILLARS.find(p => p[0] === k);
        return `<div class="pill">${meta ? meta[1] : k} <b>${v.score === null ? 'n/a' : v.score}</b> <span class="dim">/${v.weight}</span></div>`;
      }).join('')}
    </div></div>

    ${a.reasons.length ? `<div class="d-sec"><h4>Why it works</h4><ul class="why good">${a.reasons.map(x => `<li>${x}</li>`).join('')}</ul></div>` : ''}
    ${a.risks.length ? `<div class="d-sec"><h4>What could go wrong</h4><ul class="why risk">${a.risks.map(x => `<li>${x}</li>`).join('')}</ul></div>` : ''}
    ${a.red_flags.length ? `<div class="d-sec"><h4>Gates triggered</h4><ul class="why risk">${a.red_flags.map(f => `<li>${f.text}${f.severe ? ' <b>(veto)</b>' : ''}</li>`).join('')}</ul></div>` : ''}

    ${bs.periods ? `<div class="d-sec"><h4>Balance sheet</h4>${miniTable(bs.periods, bs.rows, [
      ['Equity Capital', 'Share capital'], ['Reserves', 'Reserves'], ['Borrowings', 'Borrowings'],
      ['Total Liabilities', 'Total liabilities'], ['Fixed Assets', 'Fixed assets'],
      ['Investments', 'Investments'], ['Total Assets', 'Total assets']])}</div>` : ''}

    ${pl.periods ? `<div class="d-sec"><h4>Profit &amp; loss</h4>${miniTable(pl.periods, pl.rows, [
      ['Sales', 'Revenue'], ['Operating Profit', 'EBITDA'], ['OPM %', 'OPM %'],
      ['Interest', 'Interest'], ['Depreciation', 'Depreciation'], ['Net Profit', 'Net profit'],
      ['EPS in Rs', 'EPS ₹']])}</div>` : ''}

    ${cf.periods ? `<div class="d-sec"><h4>Cash flow</h4>${miniTable(cf.periods, cf.rows, [
      ['Cash from Operating Activity', 'CFO'], ['Cash from Investing Activity', 'CFI'],
      ['Cash from Financing Activity', 'CFF'], ['Free Cash Flow', 'FCF']])}</div>` : ''}

    ${(r.shareholding || {}).periods ? `<div class="d-sec"><h4>Shareholding pattern</h4>${shpBlock(r.shareholding)}</div>` : ''}

    ${(r.insights && (r.insights.pros.length || r.insights.cons.length)) ? `<div class="d-sec">
      <h4>screener.in's own machine notes</h4>
      <ul class="why good">${r.insights.pros.map(x => `<li>${x}</li>`).join('')}</ul>
      <ul class="why risk">${r.insights.cons.map(x => `<li>${x}</li>`).join('')}</ul>
      <p class="sub" style="margin-top:8px">Generated by screener.in, shown for cross-checking against the model above.</p></div>` : ''}

    <a class="d-src" target="_blank" rel="noopener" href="https://www.screener.in/company/${encodeURIComponent(key)}/consolidated/">
      Open the source page on screener.in</a>
  `;
  $('#drawer').hidden = false;
  $('#scrim').hidden = false;
  $('#drawer').scrollTop = 0;
}

/* ---------- universe search ---------- */

async function suggest(term) {
  const box = $('#suggest');
  if (term.length < 2) { box.hidden = true; box.innerHTML = ''; return; }
  const res = await fetch('/api/search?q=' + encodeURIComponent(term));
  const items = (await res.json()).matches || [];
  box.innerHTML = items.map(m => `
    <button class="sg" data-key="${esc(m.key)}" data-ready="${m.scraped ? 1 : 0}">
      <span class="sg-name">${esc(m.name || m.key)}</span>
      <span class="sg-sym">${esc(m.key)}</span>
      <span class="sg-state">${m.scraped ? 'ready' : 'reads live'}</span>
    </button>`).join('') || '<div class="sg-empty">No listed company matches that.</div>';
  box.hidden = false;
  $$('#suggest .sg').forEach(b => b.onclick = () => openAny(b.dataset.key, b.dataset.ready === '1'));
}

async function openAny(key, ready) {
  $('#suggest').hidden = true;
  if (ready) return openDrawer(key);
  // Not crawled yet: read this one page now rather than asking the user to wait
  // for a crawl that is still hours from this symbol.
  $('#drawer').hidden = false;
  $('#scrim').hidden = false;
  $('#drawerBody').innerHTML = `<div class="d-head"><h3>${esc(key)}</h3>
    <div class="sym">Reading this company from screener.in&hellip;</div></div>`;
  const res = await fetch('/api/fetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) {
    $('#drawerBody').innerHTML = `<div class="d-head"><h3>${esc(key)}</h3>
      <div class="sym">Screener has no readable company page for that symbol.</div></div>`;
    return;
  }
  await res.json();
  await loadResults();
  openDrawer(key);
}

function closeDrawer() {
  $('#drawer').hidden = true;
  $('#scrim').hidden = true;
  history.replaceState(null, '', location.pathname);
}

/* ---------- wiring ---------- */

function bindControls() {
  const sync = () => {
    $('#vPromoter').textContent = $('#minPromoter').value + '%';
    const s = +$('#minScore').value; $('#vScore').textContent = s;
    const mc = +$('#minMcap').value; $('#vMcap').textContent = mc ? `₹${fmt0(mc)} Cr` : 'Any';
    const pe = +$('#maxPe').value; $('#vPe').textContent = pe ? `${pe}x` : 'Any';
  };
  ['minPromoter', 'minScore', 'minMcap', 'maxPe'].forEach(id => {
    $('#' + id).addEventListener('input', () => { sync(); loadResults(); });
  });
  $('#q').addEventListener('input', () => {
    const term = $('#q').value.trim();
    suggest(term);
    clearTimeout(state.t);
    state.t = setTimeout(loadResults, 260);
  });
  $('#q').addEventListener('focus', () => suggest($('#q').value.trim()));
  document.addEventListener('click', e => {
    if (!e.target.closest('.ctl-search')) $('#suggest').hidden = true;
  });
  $$('#verdicts button').forEach(b => b.onclick = () => {
    $$('#verdicts button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    state.verdict = b.dataset.v;
    loadResults();
  });
  $$('th.sortable').forEach(th => th.onclick = () => {
    const k = th.dataset.key;
    state.order = state.sort === k && state.order === 'desc' ? 'asc' : 'desc';
    state.sort = k;
    loadResults();
  });
  $('#btnScan').onclick = scrape;
  $('#topScan').onclick = () => { document.getElementById('screen').scrollIntoView({ behavior: 'smooth' }); };
  $('#heroScan').onclick = () => { scrape(); document.getElementById('screen').scrollIntoView({ behavior: 'smooth' }); };
  $('#btnStop').onclick = () => fetch('/api/scrape/stop', { method: 'POST' }).then(loadStatus);
  // The CSV carries the filters currently on screen, not the 250 rows on show.
  $('#btnCsv').onclick = () => {
    const p = params();
    p.delete('limit');
    const a = document.createElement('a');
    a.href = '/api/export?' + p.toString();
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  $('#drawerClose').onclick = closeDrawer;
  $('#scrim').onclick = closeDrawer;
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    $('#suggest').hidden = true;
    closeDrawer();
  });
  sync();
}

renderMethod();
bindControls();
// ?q=<term> pre-fills the search so a shortlist query is shareable too.
const initialQ = new URLSearchParams(location.search).get('q') || '';
if (initialQ) $('#q').value = initialQ;

loadStatus().then(() => loadResults().then(() => {
  if (initialQ) suggest(initialQ);
  const m = location.hash.match(/^#c=(.+)$/);
  if (m) openDrawer(decodeURIComponent(m[1]));
}).catch(() => {}));
startPolling();
