/* Insider Trades - static PWA.
 *
 * Reads the JSON the scheduled job writes. Two rules run through all of it:
 *   1. Never render a number the data does not contain. Missing is shown as
 *      missing, and a stale feed says so loudly.
 *   2. Every record keeps a link to the filing it came from.
 */
'use strict';

const DATA = {
  trades: 'data/trades.json',
  people: 'data/people.json',
  meta: 'data/meta.json',
  copy: 'data/copy_check.json',
};

const STALE_AFTER_HOURS = 3;

const state = {
  trades: [], people: [], meta: null, copy: null,
  loadErrors: [],
  filters: { person: '', assetClass: '', action: '', query: '' },
  route: { name: 'feed', arg: null },
};

/* ---------------- helpers ---------------- */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00Z');
  if (isNaN(d)) return esc(iso);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

function ago(isoStamp) {
  if (!isoStamp) return null;
  const then = new Date(isoStamp);
  if (isNaN(then)) return null;
  const mins = Math.floor((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

function hoursSince(isoStamp) {
  const then = new Date(isoStamp);
  if (isNaN(then)) return Infinity;
  return (Date.now() - then.getTime()) / 3600000;
}

function pct(value) {
  if (value == null || isNaN(value)) return null;
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
}

function isCrypto(t) { return t.source === 'crypto' || t.asset_class === 'crypto'; }

/* ---------------- loading ---------------- */

async function loadAll() {
  state.loadErrors = [];
  const bust = `?v=${Date.now()}`;
  const results = await Promise.all(Object.entries(DATA).map(async ([key, path]) => {
    try {
      const res = await fetch(path + bust, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return [key, await res.json()];
    } catch (err) {
      state.loadErrors.push({ key, path, message: err.message });
      return [key, null];
    }
  }));
  for (const [key, value] of results) {
    if (value === null) continue;
    if (key === 'trades') state.trades = value;
    else if (key === 'people') state.people = value;
    else state[key] = value;
  }
}

/* ---------------- freshness ---------------- */

function renderFreshness() {
  const el = $('#freshness');
  const stamp = state.meta && state.meta.generated_at;

  if (!stamp) {
    el.className = 'freshness stale';
    el.textContent = state.loadErrors.length
      ? 'Data unavailable — could not load the feed. Showing nothing rather than something wrong.'
      : 'Data unavailable — no last-updated timestamp.';
    return;
  }

  const hours = hoursSince(stamp);
  const when = ago(stamp) || stamp;
  if (hours > STALE_AFTER_HOURS) {
    el.className = 'freshness stale';
    el.textContent = `Data may be stale — last successful update ${when}. `
      + `The scheduled job may have stopped; see the Sources tab.`;
  } else {
    el.className = 'freshness';
    const c = state.meta.counts || {};
    el.textContent = `Updated ${when} · ${c.parsed_trades || 0} trades · `
      + `${c.linked_documents || 0} linked documents`;
  }
}

/* ---------------- cards ---------------- */

function tradeCard(t) {
  if (!t.parsed) return documentCard(t);

  const action = t.action === 'buy' || t.action === 'sell' ? t.action : 'other';
  const label = t.action === 'buy' ? 'Buy' : t.action === 'sell' ? 'Sell'
    : (t.transaction_code_label || t.action || 'Other');

  let amount = '';
  if (t.amount_label) {
    amount = `<div class="amount">${esc(t.amount_label)}
      <small>range as reported — not an exact figure</small></div>`;
  } else if (t.shares != null) {
    const price = t.price_per_share ? ` @ $${Number(t.price_per_share).toLocaleString(undefined, { maximumFractionDigits: 4 })}` : '';
    const value = t.value_usd ? `<small>$${Number(t.value_usd).toLocaleString(undefined, { maximumFractionDigits: 0 })} total as reported</small>` : '';
    amount = `<div class="amount">${Number(t.shares).toLocaleString()} shares${esc(price)}${value}</div>`;
  }

  const notes = [];
  if (t.date_anomaly) notes.push(`<div class="note flag">⚠ ${esc(t.date_anomaly)}. Shown exactly as filed.</div>`);
  if (t.advisor_directed) notes.push(`<div class="note warn">Filing states this trade was made by an independent advisor without the filer's input or foreknowledge.</div>`);
  else if (t.endnote) notes.push(`<div class="note">Filing endnote: ${esc(t.endnote)}</div>`);
  if (t.filer_note) notes.push(`<div class="note">Filer note: ${esc(t.filer_note)}</div>`);
  if (t.withdrawn) notes.push(`<div class="note flag">Withdrawn by the filer in an amendment — not a completed trade.</div>`);
  if (t.superseded) notes.push(`<div class="note">Re-disclosure of an earlier filing.</div>`);
  if (t.shares_owned_after != null) {
    notes.push(`<div class="note">Held after this transaction: <b>${Number(t.shares_owned_after).toLocaleString()}</b> shares (as reported on the filing).</div>`);
  }
  if (!t.copy_eligible && (t.copy_ineligible_reasons || []).length) {
    notes.push(`<div class="note">Not scored in the copy check: ${esc(t.copy_ineligible_reasons.join('; '))}.</div>`);
  }

  const owner = t.owner && t.owner !== 'self'
    ? ` · ${esc(t.owner.replace('_', ' '))}-owned` : '';

  return `<article class="card">
    <div class="card-head">
      <div>
        <div class="person"><a href="#/person/${encodeURIComponent(t.person_id || '')}">${esc(t.person || 'Unknown')}</a></div>
        <div class="role">${esc(t.role || t.source_label || '')}${owner}</div>
      </div>
      <div class="action ${action}">${esc(label)}</div>
    </div>
    <div class="asset">
      ${t.ticker ? `<span class="ticker">${esc(t.ticker)}</span>` : ''}
      ${esc(t.asset_name || '—')}
    </div>
    ${amount}
    <div class="dates">
      <div class="date-item"><span>Traded</span><b>${fmtDate(t.trade_date)}</b></div>
      <div class="date-item"><span>Disclosed</span><b>${fmtDate(t.disclosure_date)}</b></div>
      <div class="date-item"><span>Delay</span><b>${t.delay_days == null ? '—' : t.delay_days + ' days'}</b></div>
    </div>
    ${notes.join('')}
    <a class="source-link" href="${esc(t.source_url || '#')}" target="_blank" rel="noopener">
      View original filing ↗</a>
  </article>`;
}

function documentCard(t) {
  const reasons = {
    scanned_image: 'Scanned filing with no readable text — not machine-parsed',
    unreliable_text_layer: 'Scanned filing with a damaged text layer — not machine-parsed',
    no_rows_parsed: 'No transaction rows could be read from this filing',
    notice_only: 'Notice of intent to sell, not a completed trade',
    fetch_error: 'Could not be downloaded',
    parse_error: 'Could not be parsed',
    not_a_pdf: 'File was not a readable PDF',
  };
  const why = reasons[t.unparsed_reason] || 'Not machine-parsed';

  return `<article class="card doc-card">
    <div class="card-head">
      <div>
        <div class="person"><a href="#/person/${encodeURIComponent(t.person_id || '')}">${esc(t.person || t.issuer || 'Unknown')}</a></div>
        <div class="role">${esc(t.role || t.source_label || '')}</div>
      </div>
      <div class="doc-badge">Document</div>
    </div>
    <div class="asset">${esc(t.title || t.source_label || 'Filing')}</div>
    <div class="note warn">${esc(why)}.
      ${t.unparsed_detail ? esc(t.unparsed_detail) + '. ' : ''}
      Read it at the source rather than trusting a guess.</div>
    <div class="dates">
      <div class="date-item"><span>Disclosed</span><b>${fmtDate(t.disclosure_date)}</b></div>
    </div>
    <a class="source-link" href="${esc(t.source_url || '#')}" target="_blank" rel="noopener">
      Open the filing ↗</a>
  </article>`;
}

/* ---------------- views ---------------- */

function applyFilters(list) {
  const f = state.filters;
  return list.filter((t) => {
    if (f.person && t.person_id !== f.person) return false;
    if (f.action && t.action !== f.action) return false;
    if (f.assetClass === 'crypto' && !isCrypto(t)) return false;
    if (f.assetClass === 'stock' && isCrypto(t)) return false;
    if (f.query) {
      const hay = `${t.person || ''} ${t.ticker || ''} ${t.asset_name || ''}`.toLowerCase();
      if (!hay.includes(f.query.toLowerCase())) return false;
    }
    return true;
  });
}

function viewFeed() {
  if (!state.trades.length) {
    return `<div class="empty">No data loaded.<br>
      ${state.loadErrors.length ? 'The feed files could not be fetched.' : 'Run the fetchers to populate the feed.'}</div>`;
  }

  const people = [...new Map(state.trades
    .filter((t) => t.person_id)
    .map((t) => [t.person_id, t.person])).entries()]
    .sort((a, b) => String(a[1]).localeCompare(String(b[1])));

  const f = state.filters;
  const rows = applyFilters(state.trades);

  return `
    <div class="filters">
      <input id="q" type="search" placeholder="Search person, ticker or asset" value="${esc(f.query)}">
      <select id="f-person">
        <option value="">All people</option>
        ${people.map(([id, name]) =>
          `<option value="${esc(id)}"${f.person === id ? ' selected' : ''}>${esc(name)}</option>`).join('')}
      </select>
    </div>
    <div class="chip-row" style="margin-bottom:12px">
      <button class="chip" data-filter="action" data-value="" aria-pressed="${!f.action}">All</button>
      <button class="chip" data-filter="action" data-value="buy" aria-pressed="${f.action === 'buy'}">Buys</button>
      <button class="chip" data-filter="action" data-value="sell" aria-pressed="${f.action === 'sell'}">Sells</button>
      <button class="chip" data-filter="assetClass" data-value="stock" aria-pressed="${f.assetClass === 'stock'}">Stocks</button>
      <button class="chip" data-filter="assetClass" data-value="crypto" aria-pressed="${f.assetClass === 'crypto'}">Crypto</button>
    </div>
    <div class="result-count">${rows.length} of ${state.trades.length} records</div>
    ${rows.length ? rows.slice(0, 300).map(tradeCard).join('')
      : '<div class="empty">No records match these filters.</div>'}
    ${rows.length > 300 ? `<div class="result-count">Showing the newest 300. Narrow the filters to see more.</div>` : ''}`;
}

function viewPeople() {
  if (!state.people.length) return '<div class="empty">No people loaded.</div>';
  return `<div class="result-count">${state.people.length} people</div>` +
    state.people.map((p) => `
      <a class="person-row" href="#/person/${encodeURIComponent(p.person_id)}">
        <div>
          <div class="person">${esc(p.name || p.person_id)}</div>
          <small>${esc((p.roles || [])[0] || '')}${p.positions && p.positions.length ? ` · ${p.positions.length} reported position${p.positions.length === 1 ? '' : 's'}` : ''}</small>
        </div>
        <div class="count-pill">${p.trade_count}${p.document_count ? ` + ${p.document_count}📄` : ''}</div>
      </a>`).join('');
}

function viewPerson(id) {
  const person = state.people.find((p) => p.person_id === id);
  const trades = state.trades.filter((t) => t.person_id === id);
  if (!person && !trades.length) {
    return `<a class="back" href="#/people">← People</a><div class="empty">Person not found.</div>`;
  }
  const name = person ? person.name : (trades[0] && trades[0].person) || id;

  let positions;
  if (person && person.positions && person.positions.length) {
    positions = `<table class="positions">
      <thead><tr><th>Security</th><th>Shares held</th><th>As of</th></tr></thead>
      <tbody>${person.positions.map((pos) => `
        <tr>
          <td>${esc(pos.ticker || pos.security || '—')}${pos.security_kind === 'derivative' ? ' <small>(derivative)</small>' : ''}</td>
          <td>${Number(pos.shares_owned).toLocaleString()}</td>
          <td>${fmtDate(pos.as_of_trade_date)}</td>
        </tr>`).join('')}</tbody></table>
      <div class="note">Positions come from Form 4's "shares owned following transaction".
        They are accurate as of that filing, not today.</div>`;
  } else {
    positions = `<div class="note">Not disclosed. This person's sources report individual
      transactions but no running holdings, so there is nothing to show here.</div>`;
  }

  const stats = state.copy && (state.copy.people || []).find((p) => p.person_id === id);
  let copyBlock = '';
  if (stats) {
    const today = (stats.by_horizon || {}).today || {};
    copyBlock = `<div class="section-title">Copy check</div>${personStats(stats, today)}`;
  }

  return `<a class="back" href="#/people">← People</a>
    <h2 style="margin:0 0 4px;font-size:20px">${esc(name)}</h2>
    <div class="role" style="margin-bottom:14px">${esc((person && (person.roles || [])[0]) || '')}</div>
    <div class="section-title">Current positions</div>
    ${positions}
    ${copyBlock}
    <div class="section-title">Trade history (${trades.length})</div>
    ${trades.map(tradeCard).join('')}`;
}

function personStats(stats, today) {
  if (today.status !== 'ok') {
    return `<div class="note">Not enough measurable buys to report a win rate
      (${today.n || 0} of the ${today.needed || 5} needed). Showing a percentage from
      this few trades would be noise, not signal.</div>`;
  }
  return `<div class="stat-row">
      <div class="stat"><span>Win rate vs benchmark</span><b>${today.win_rate_vs_benchmark_pct}%</b></div>
      <div class="stat"><span>Avg return</span><b class="${today.avg_return_pct >= 0 ? 'pos' : 'neg'}">${pct(today.avg_return_pct)}</b></div>
      <div class="stat"><span>Avg vs benchmark</span><b class="${today.avg_excess_pct >= 0 ? 'pos' : 'neg'}">${pct(today.avg_excess_pct)}</b></div>
      <div class="stat"><span>Measured buys</span><b>${stats.measured_buys}</b></div>
    </div>
    ${stats.underlying_filing_rows > stats.measured_buys
      ? `<div class="note">${stats.measured_buys} distinct decisions from
         ${stats.underlying_filing_rows} filing rows — the same purchase reported across
         several accounts counts once.</div>` : ''}`;
}

function viewCopy() {
  if (!state.copy) return '<div class="empty">Copy check unavailable — data not loaded.</div>';
  const m = state.copy.method || {};
  const c = state.copy.counts || {};

  const method = `<div class="method">
    <b>How this is measured</b>
    <ul>
      <li>Baseline: ${esc(m.baseline || '')}. That is the earliest you could have acted.</li>
      <li>Horizons: +${(m.horizons_days || []).join('d, +')}d and to today.</li>
      <li>Benchmark: ${esc((m.benchmarks || {}).stocks || 'SPY')} for stocks,
          ${esc((m.benchmarks || {}).crypto || 'BTC')} for crypto, over the same window.</li>
      <li>${esc(m.measures || '')}.</li>
      <li>${esc(m.deduplication || '')}.</li>
      <li>Below ${m.min_trades_for_stats || 5} measurable buys, no win rate is shown.</li>
    </ul>
    <p style="margin:9px 0 0"><i>${esc(m.disclaimer || '')}</i></p>
  </div>`;

  const people = (state.copy.people || []).map((p) => {
    const today = (p.by_horizon || {}).today || {};
    return `<div class="card">
      <div class="person"><a href="#/person/${encodeURIComponent(p.person_id)}">${esc(p.person)}</a></div>
      ${personStats(p, today)}
    </div>`;
  }).join('');

  const measured = (state.copy.trades || []).filter((t) => t.measurable).slice(0, 60);
  const trades = measured.map((t) => {
    const cells = ['d7', 'd30', 'd90', 'today'].map((key) => {
      const h = (t.horizons || {})[key] || {};
      const title = key === 'today' ? 'Today' : '+' + key.slice(1) + 'd';
      if (h.status !== 'ok') {
        return `<div class="h-cell"><span>${title}</span><b class="na">${h.status === 'pending' ? 'not yet' : 'n/a'}</b></div>`;
      }
      return `<div class="h-cell"><span>${title}</span>
        <b class="${h.excess_pct >= 0 ? 'pos' : 'neg'}">${pct(h.excess_pct)}</b></div>`;
    }).join('');

    return `<article class="card">
      <div class="card-head">
        <div><div class="person">${esc(t.person)}</div>
          <div class="role">Bought · disclosed ${fmtDate(t.disclosure_date)}</div></div>
        <div class="action buy">${esc(t.ticker)}</div>
      </div>
      <div class="role" style="margin-top:8px">
        Baseline ${fmtDate(t.baseline_date)} at $${Number(t.baseline_price).toLocaleString(undefined, { maximumFractionDigits: 2 })}
        · vs ${esc(t.benchmark)}</div>
      <div class="horizons">${cells}</div>
    </article>`;
  }).join('');

  const unmeasurable = (state.copy.trades || []).filter((t) => !t.measurable);
  const unmeasurableBlock = unmeasurable.length ? `
    <div class="section-title">Could not be measured (${unmeasurable.length})</div>
    ${unmeasurable.slice(0, 20).map((t) => `<div class="card">
      <div class="person">${esc(t.person)} · ${esc(t.ticker || '—')}</div>
      <div class="note">${esc(t.reason || 'unmeasurable')}</div></div>`).join('')}` : '';

  return `${method}
    <div class="result-count">${c.measured || 0} of ${c.eligible_buys || 0} eligible buys measured
      · figures shown are performance against the benchmark</div>
    <div class="section-title">By person</div>
    ${people || '<div class="empty">No per-person statistics yet.</div>'}
    <div class="section-title">Individual buys</div>
    ${trades || '<div class="empty">No measurable buys yet.</div>'}
    ${unmeasurableBlock}`;
}

function viewSources() {
  const sources = (state.meta && state.meta.sources) || {};
  const names = {
    house_clerk_ptr: 'U.S. House Clerk — Periodic Transaction Reports',
    sec_form4: 'SEC EDGAR — Form 4 / Form 144',
    oge_278t: 'OGE Form 278-T — whitehouse.gov',
    crypto: 'On-chain wallets — Blockscout',
    prices: 'Price data — Yahoo Finance & CoinGecko',
    copy_check: 'Copy check computation',
  };

  const entries = Object.entries(sources);
  const cards = entries.map(([key, s]) => `
    <div class="src">
      <div class="src-head">
        <b>${esc(names[key] || key)}</b>
        <span class="pill ${s.ok ? 'ok' : 'down'}">${s.ok ? 'OK' : 'FAILING'}</span>
      </div>
      <p>${s.ok
        ? `Last successful update ${esc(ago(s.last_success) || s.last_success || 'unknown')}.`
        : `<b>Unavailable.</b> Last successful update ${esc(ago(s.last_success) || s.last_success || 'never')}.`}</p>
      ${s.detail ? `<p>${esc(s.detail)}</p>` : ''}
    </div>`).join('');

  const errors = state.loadErrors.length ? `
    <div class="section-title">Feed files that failed to load</div>
    ${state.loadErrors.map((e) => `<div class="src">
      <div class="src-head"><b>${esc(e.path)}</b><span class="pill down">ERROR</span></div>
      <p>${esc(e.message)}</p></div>`).join('')}` : '';

  return `${cards || '<div class="empty">No source status recorded yet.</div>'}
    ${errors}
    <div class="method" style="margin-top:16px">
      <b>Sources deliberately not included</b>
      <ul>
        <li><b>Senate eFD.</b> Requires personally affirming the Ethics in Government
            Act prohibitions and blocks automated access. Not something a script
            should click on your behalf.</li>
        <li><b>The President's own 278-T filings.</b> Published as scanned images
            whose text layer is too damaged to read reliably. They appear in the
            feed as documents you can open, never as parsed trades.</li>
      </ul>
    </div>`;
}

/* ---------------- routing ---------------- */

function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [name, arg] = hash.split('/');
  state.route = { name: name || 'feed', arg: arg ? decodeURIComponent(arg) : null };
}

function render() {
  renderFreshness();
  const { name, arg } = state.route;
  const view = $('#view');

  let html;
  if (name === 'people') html = viewPeople();
  else if (name === 'person') html = viewPerson(arg);
  else if (name === 'copy') html = viewCopy();
  else if (name === 'sources') html = viewSources();
  else html = viewFeed();

  view.innerHTML = html;

  const activeTab = name === 'person' ? 'people' : name;
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.setAttribute('aria-selected', String(tab.dataset.route === activeTab));
  });
  window.scrollTo({ top: 0 });
  wireViewEvents();
}

function wireViewEvents() {
  const q = $('#q');
  if (q) {
    let timer;
    q.addEventListener('input', (e) => {
      clearTimeout(timer);
      const value = e.target.value;
      timer = setTimeout(() => {
        state.filters.query = value;
        const pos = q.selectionStart;
        render();
        const again = $('#q');
        if (again) { again.focus(); again.setSelectionRange(pos, pos); }
      }, 220);
    });
  }

  const personSelect = $('#f-person');
  if (personSelect) {
    personSelect.addEventListener('change', (e) => {
      state.filters.person = e.target.value;
      render();
    });
  }

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const { filter, value } = chip.dataset;
      state.filters[filter] = state.filters[filter] === value ? '' : value;
      render();
    });
  });
}

function wireChrome() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => { location.hash = `#/${tab.dataset.route}`; });
  });
  $('#refresh').addEventListener('click', async () => {
    $('#refresh').textContent = '…';
    await loadAll();
    $('#refresh').innerHTML = '&#8635;';
    render();
  });
  window.addEventListener('hashchange', () => { parseRoute(); render(); });
}

async function main() {
  wireChrome();
  parseRoute();
  await loadAll();
  render();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* offline cache is optional */ });
  }
}

main();
