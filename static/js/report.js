/* The report window (display_report, design spec §3): a modal <dialog> with
   KPI tiles, a chart, a native sortable table and markdown notes, in French.
   - showModal(): Échap closes it natively, the page behind is inert, and the
     focus goes back to whatever opened it.
   - ApexCharts is fetched only when a report with a chart opens, from the
     same pinned URL and integrity hash as before (no Grid.js any more: it
     failed now and then with 'An error happened while fetching the data').
   - Everything from the model is text: KPI and table cells go in through
     textContent, the notes through md() (sanitised), and chart labels are
     neutralised before ApexCharts writes them into its legend and tooltip. */
import { $, md, settings } from "./core.js";
import { T, fmtNumber } from "./strings-fr.js";
import { toast } from "./hud.js";

const R = T.report;

// Pinned with its integrity hash, as it was in index.html (SRI, design spec).
const APEX = {
  src: "https://cdn.jsdelivr.net/npm/apexcharts@7.4.0/dist/apexcharts.min.js",
  integrity: "sha384-fnhrfzODrKsQTTXTYoDIc5f/SIP1KuiO4hz6PmkcIBHYVaAj8QpSOtByqodkU2iO",
};
const APEX_TIMEOUT_MS = 15000;
const CHART_TYPES = new Set(["line", "bar", "area", "donut"]);
// Series palette validated for dark surfaces (CVD-safe, 5.6-6.4:1).
const SERIES_COLORS = ["#1e93c4", "#c47b1e", "#1ea86a"];
const PAGE = 25;          // table rows per page
const MAX_ROWS = 2000;    // a runaway table never freezes the page

let rep, opener = null;
let chart = null;         // the ApexCharts instance on screen
let shownOptions = null;  // what it was given (tests read it: reduced motion)
let renderSeq = 0;        // a newer report wins over a chart still loading
let table = null;         // {columns, rows, sortCol, dir, page, years}
let lastBtn = null;

/* ---------------------------------------------------------- numbers, the French way */
/* A number from the model, or from a cell typed as text: 1234.5, '1 234,5',
   '12 480 €', '+12 %', '1,234.56'. null when it is not one. */
export function toNumber(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string") return null;
  let s = v.replace(/[\s\u00a0\u202f]/g, "").replace(/^\+/, "").replace(/\u2212/g, "-")
    .replace(/(%|€|\$|£)$/, "").replace(/^(€|\$|£)/, "");
  const comma = s.lastIndexOf(","), dot = s.lastIndexOf(".");
  if (comma >= 0 && dot >= 0) {  // both: the last one is the decimal separator
    s = comma > dot ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
  } else if (comma >= 0) {
    s = (s.match(/,/g).length > 1) ? s.replace(/,/g, "") : s.replace(",", ".");
  } else if ((s.match(/\./g) || []).length > 1) {
    s = s.replace(/\./g, "");  // 1.234.567
  }
  return /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : null;
}

// '1 234,5' (design spec §5); a year stays '2026', never '2 026'.
const num = (v, { years = false } = {}) => (typeof v === "number" && Number.isFinite(v)
  ? fmtNumber(v === 0 ? 0 : v, years ? { useGrouping: false } : {}) : v);  // never '-0'
const YEAR_HEADER = /^(ann[ée]es?|ans?|years?|exercices?|mill[ée]simes?)$/i;

// ApexCharts writes series names, categories and values into its legend and
// tooltip as HTML: a label never carries a character that opens a tag or
// leaves an attribute, and a value is a number or nothing.
export const label = (v) => String(v ?? "").replace(/</g, "‹").replace(/>/g, "›")
  .replace(/"/g, "”").replace(/'/g, "’").replace(/`/g, "ʼ");
const value = (v) => {
  const n = toNumber(v);
  return n === null ? null : n;
};

function reducedMotion() {
  try {
    return matchMedia("(prefers-reduced-motion: reduce)").matches || settings.get("motion") === "reduced";
  } catch { return false; }
}

/* ---------------------------------------------------------- ApexCharts, on demand */
let apexLoading = null;

/* The library, loaded once from its pinned URL with its integrity hash; null
   offline or when the CDN refuses (a later report tries again). Settings'
   cost chart (WP18) loads it through here too. */
export function loadApexCharts() {
  if (window.ApexCharts) return Promise.resolve(window.ApexCharts);
  if (!apexLoading) {
    apexLoading = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = APEX.src;
      s.integrity = APEX.integrity;
      s.crossOrigin = "anonymous";
      s.referrerPolicy = "no-referrer";
      s.async = true;
      s.dataset.lazy = "apexcharts";
      s.onload = () => resolve(window.ApexCharts || null);
      s.onerror = () => { s.remove(); apexLoading = null; resolve(null); };
      document.head.append(s);  // never in <body>: the page's own content stays script-free
    });
  }
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), APEX_TIMEOUT_MS));
  return Promise.race([apexLoading, timeout]);
}

/* ApexCharts' own words (months, days, toolbar) in French. */
let frLocale = null;
function frenchLocale() {
  if (frLocale) return frLocale;
  const fmt = (opts) => new Intl.DateTimeFormat("fr-FR", opts);
  const months = Array.from({ length: 12 }, (_, m) => new Date(2021, m, 1));
  const days = Array.from({ length: 7 }, (_, d) => new Date(2021, 0, 3 + d));  // 3 Jan 2021 is a Sunday
  frLocale = {
    name: "fr",
    options: {
      months: months.map(d => fmt({ month: "long" }).format(d)),
      shortMonths: months.map(d => fmt({ month: "short" }).format(d)),
      days: days.map(d => fmt({ weekday: "long" }).format(d)),
      shortDays: days.map(d => fmt({ weekday: "short" }).format(d)),
      toolbar: R.toolbar,
    },
  };
  return frLocale;
}

function cssVar(name, fallback) {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  } catch { return fallback; }
}

/* {opts, names} for ApexCharts, or null when there is nothing to draw. */
function chartOptions(c) {
  const type = CHART_TYPES.has(c.type) ? c.type : "line";
  const donut = type === "donut";
  const series = (Array.isArray(c.series) ? c.series : []).filter(s => s && typeof s === "object").slice(0, 3)
    .map(s => ({ name: label(s.name), data: (Array.isArray(s.data) ? s.data : []).slice(0, 500).map(value) }));
  const categories = (Array.isArray(c.categories) ? c.categories : []).slice(0, 500).map(label);
  if (!series.length || !series.some(s => s.data.some(v => v !== null))) return null;
  const names = donut ? categories : series.map(s => s.name);
  const fmt = (v) => (v === null || v === undefined ? "" : num(v));
  const opts = {
    chart: {
      type, height: 280, background: "transparent", toolbar: { show: false },
      foreColor: cssVar("--muted", "#7aa7c2"), fontFamily: "Rajdhani, sans-serif",
      animations: { enabled: !reducedMotion() },
      locales: [frenchLocale()], defaultLocale: "fr",
      // The svg is an image named in French: no keyboard navigation (its
      // focusable svg and legend buttons speak English, and nest inside the
      // chart). The figures are in the table, reachable with the keyboard.
      accessibility: { description: R.chartLabel(names.filter(Boolean).slice(0, 6).join(", ")),
                       keyboard: { enabled: false } },
    },
    theme: { mode: "dark" },
    colors: SERIES_COLORS,
    stroke: donut ? { width: 1, colors: [cssVar("--panel", "#07141e")] } : { width: 2, curve: "straight" },
    grid: { borderColor: "rgba(64,220,255,.12)", strokeDashArray: 3 },
    dataLabels: { enabled: false },
    legend: { show: names.length > 1, labels: { colors: cssVar("--text", "#cfeaff") } },
    tooltip: { theme: "dark", y: { formatter: fmt } },
    series: donut ? series[0].data.map(v => v ?? 0) : series,
  };
  if (donut) {
    opts.labels = categories;
  } else {
    opts.xaxis = { categories };
    opts.yaxis = { labels: { formatter: fmt } };
  }
  if (type === "area") opts.fill = { type: "gradient", gradient: { opacityFrom: .3, opacityTo: .02 } };
  return { opts, names };
}

function setChartState(el, state, text = "") {
  el.dataset.state = state;
  el.setAttribute("aria-busy", String(state === "loading"));
  if (text) {
    const p = document.createElement("p");
    p.className = "rchart-note";
    p.textContent = text;
    el.replaceChildren(p);
  }
}

function destroyChart() {
  if (!chart) return;
  try { chart.destroy(); } catch { /* already gone */ }
  chart = null;
}

async function renderChart(c, seq) {
  const el = $("rchart");
  destroyChart();
  el.replaceChildren();
  delete el.dataset.state;
  for (const a of ["aria-busy", "role", "aria-label"]) el.removeAttribute(a);
  el.hidden = true;
  const made = c && typeof c === "object" ? chartOptions(c) : null;
  if (!made) return;
  const { opts } = made;
  el.hidden = false;
  setChartState(el, "loading", R.chartLoading);
  const Apex = await loadApexCharts();
  if (seq !== renderSeq) return;  // another report replaced this one meanwhile
  if (!Apex) {
    setChartState(el, "unavailable", R.chartUnavailable);
    return;
  }
  el.replaceChildren();
  shownOptions = opts;
  try {
    chart = new Apex(el, opts);
    await chart.render();
  } catch (err) {
    // A locale this version would not take: the same chart, in its own words.
    console.warn(err);
    destroyChart();
    el.replaceChildren();
    try {
      delete opts.chart.locales; delete opts.chart.defaultLocale;
      chart = new Apex(el, opts);
      await chart.render();
    } catch (err2) {
      console.warn(err2);
      destroyChart();
      setChartState(el, "unavailable", R.chartFailed);
      return;
    }
  }
  if (seq !== renderSeq) return;
  setChartState(el, "ready");
  // Named by its svg (chart.accessibility above): a role here would nest it.
}

/* The options of the chart on screen (null when there is none). */
export function shownChartOptions() {
  return chart ? shownOptions : null;
}

/* ---------------------------------------------------------- KPI tiles */
function renderKpis(kpis) {
  const el = $("kpis");
  el.replaceChildren();
  for (const k of (Array.isArray(kpis) ? kpis : []).filter(k => k && typeof k === "object").slice(0, 4)) {
    const tile = document.createElement("div");
    tile.className = "kpi";
    const l = document.createElement("div");
    l.className = "l";
    l.textContent = String(k.label ?? "");
    const v = document.createElement("div");
    v.className = "v";
    v.textContent = String(num(k.value) ?? "");
    tile.append(l, v);
    if (k.delta !== undefined && k.delta !== null && String(k.delta).trim()) {
      const d = document.createElement("div");
      const text = String(k.delta).trim();
      d.className = /^[-\u2212]/.test(text) ? "d neg" : "d";
      d.textContent = text;
      tile.append(d);
    }
    el.append(tile);
  }
  el.hidden = !el.children.length;
}

/* ---------------------------------------------------------- the table */
const collator = new Intl.Collator("fr", { numeric: true, sensitivity: "base" });
const isEmpty = (v) => v === null || v === undefined || String(v).trim() === "";

/* Numbers by value, text in French alphabetical order (numbers first), empty
   cells last whatever the direction. */
export function compareCells(a, b, dir = 1) {
  const ea = isEmpty(a), eb = isEmpty(b);
  if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1;
  const na = toNumber(a), nb = toNumber(b);
  let c;
  if (na !== null && nb !== null) c = na - nb;
  else if (na !== null) c = -1;
  else if (nb !== null) c = 1;
  else c = collator.compare(String(a), String(b));
  return c * dir;
}

function cellText(v, col) {
  if (isEmpty(v)) return "";
  if (typeof v === "number") return String(num(v, { years: table.years[col] }));
  if (typeof v === "boolean") return v ? R.yes : R.no;
  if (typeof v === "object") return "";  // the schema says string or number
  return String(v);
}

function sortedRows() {
  const { rows, sortCol, dir } = table;
  if (sortCol < 0) return rows;
  return rows.map((r, i) => [r, i])
    .sort((x, y) => compareCells(x[0][sortCol], y[0][sortCol], dir) || x[1] - y[1])
    .map(([r]) => r);
}

function renderTable(tb) {
  const el = $("rtable");
  el.replaceChildren();
  table = null;
  const rows = (Array.isArray(tb?.rows) ? tb.rows : []).filter(Array.isArray).slice(0, MAX_ROWS);
  if (!rows.length) { el.hidden = true; return; }
  el.hidden = false;
  const given = (Array.isArray(tb.columns) ? tb.columns : []).map(c => String(c ?? ""));
  const width = Math.min(30, Math.max(given.length, ...rows.map(r => r.length)));
  const columns = Array.from({ length: width }, (_, i) => given[i] || R.column(i + 1));
  table = {
    columns, rows: rows.map(r => Array.from({ length: width }, (_, i) => r[i])),
    sortCol: -1, dir: 1, page: 0,
    years: columns.map(c => YEAR_HEADER.test(c.trim())),
  };

  const bar = document.createElement("div");
  bar.className = "rtable-bar";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "ctl";
  copy.textContent = R.copyCsv;
  copy.setAttribute("aria-label", R.copyCsvLabel);
  copy.addEventListener("click", copyCsv);
  bar.append(copy);

  const wrap = document.createElement("div");
  wrap.className = "rtable-wrap";
  wrap.tabIndex = 0;  // a scroller the keyboard reaches
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-label", R.tableLabel);
  const t = document.createElement("table");
  const thead = t.createTHead();
  const hr = thead.insertRow();
  columns.forEach((name, i) => {
    const th = document.createElement("th");
    th.scope = "col";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sort";
    const span = document.createElement("span");
    span.textContent = name;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.setAttribute("aria-hidden", "true");
    b.append(span, arrow);
    b.addEventListener("click", () => sortBy(i));
    th.append(b);
    hr.append(th);
  });
  t.createTBody();
  wrap.append(t);

  // Précédent · Lignes 1 à 25 sur 60 · Suivant: built once, so the focus stays on the button.
  const pager = document.createElement("div");
  pager.className = "rtable-pager";
  const nav = (text, step) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ctl";
    b.textContent = text;
    b.addEventListener("click", () => {
      table.page += step;
      drawRows();
      // On the first or last page this button is disabled: its twin takes the focus.
      if (b.disabled) (step < 0 ? b.parentElement.lastElementChild : b.parentElement.firstElementChild).focus();
    });
    return b;
  };
  const info = document.createElement("span");
  info.className = "rtable-info";
  info.setAttribute("aria-live", "polite");
  pager.append(nav(R.previous, -1), info, nav(R.next, 1));
  el.append(bar, wrap, pager);
  drawRows();
}

function sortBy(col) {
  if (!table) return;
  table.dir = table.sortCol === col ? -table.dir : 1;
  table.sortCol = col;
  table.page = 0;
  drawRows();
}

function drawRows() {
  const el = $("rtable");
  const t = el.querySelector("table");
  if (!t || !table) return;
  t.querySelectorAll("thead th").forEach((th, i) => {
    const on = i === table.sortCol;
    if (on) th.setAttribute("aria-sort", table.dir > 0 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
    th.querySelector(".arrow").textContent = on ? (table.dir > 0 ? "▲" : "▼") : "↕";
  });
  const rows = sortedRows();
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  table.page = Math.min(table.page, pages - 1);
  const from = table.page * PAGE;
  const shown = rows.slice(from, from + PAGE);
  const body = t.tBodies[0];
  body.replaceChildren(...shown.map((r) => {
    const tr = document.createElement("tr");
    r.forEach((v, i) => {
      const td = document.createElement("td");
      td.textContent = cellText(v, i);
      if (typeof v === "number" || (typeof v === "string" && toNumber(v) !== null)) td.className = "num";
      tr.append(td);
    });
    return tr;
  }));
  const pager = el.querySelector(".rtable-pager");
  pager.hidden = pages < 2;
  const [prev, info, next] = pager.children;
  prev.disabled = table.page === 0;
  next.disabled = table.page >= pages - 1;
  info.textContent = R.rows(from + 1, from + shown.length, rows.length);
}

/* CSV the way a French spreadsheet reads it: ';' between cells, a decimal
   comma, and no cell that a spreadsheet would run as a formula. */
export function toCsv(columns, rows) {
  const cell = (v) => {
    if (isEmpty(v)) return "";
    let s = typeof v === "number" ? String(v).replace(".", ",") : String(v);
    if (typeof v !== "number" && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns, ...rows].map(r => r.map(cell).join(";")).join("\r\n");
}

async function copyCsv() {
  if (!table) return;
  try {
    await navigator.clipboard.writeText(toCsv(table.columns, sortedRows()));
    toast(T.controls.copied, { ms: 2500 });
  } catch {
    toast(T.hud.copyFailed);
  }
}

/* ---------------------------------------------------------- the dialog */
export function showReport(r) {
  if (!rep) return;
  r = r && typeof r === "object" ? r : {};
  window.lastReport = r;
  if (lastBtn) lastBtn.hidden = false;
  const seq = ++renderSeq;
  $("rtitle").textContent = String(r.title ?? "").trim() || R.untitled;
  renderKpis(r.kpis);
  renderTable(r.table);
  const notes = $("rmd");
  notes.innerHTML = typeof r.markdown === "string" && r.markdown.trim() ? md(r.markdown) : "";
  notes.hidden = !notes.innerHTML;
  renderChart(r.chart, seq).catch((err) => console.warn(err));
  open();
  rep.scrollTop = 0;  // a new report starts at its top, even in a window already open
}

function isModal() {
  try { return rep.matches(":modal"); } catch { return rep.open; }
}

function open() {
  if (rep.open && isModal()) return;  // already shown: the content was replaced
  const active = document.activeElement;
  if (!rep.contains(active)) opener = active;
  if (rep.open) rep.close();  // opened in place (old code, tests): reopen as a modal
  try {
    rep.showModal();
  } catch (err) {
    console.warn(err);
    rep.setAttribute("open", "");  // not connected or not supported: shown all the same
  }
}

export function closeReport() {
  if (rep?.open) rep.close();
}

function onClose() {
  if (rep.open) return;  // closed then reopened at once: this late event is stale
  renderSeq++;  // a chart still loading is not drawn into a closed window
  destroyChart();
  const back = opener;
  opener = null;
  // The browser gives the focus back itself; when the opener is gone (a card
  // replaced meanwhile, the page itself), the orb takes it, never <body>.
  const lost = !document.activeElement || document.activeElement === document.body
    || rep.contains(document.activeElement);
  if (!lost) return;
  if (back && back.isConnected && back !== document.body && !back.closest("[inert]")
      && typeof back.focus === "function") {
    back.focus({ preventScroll: true });
    if (document.activeElement === back) return;
  }
  $("orbBtn")?.focus({ preventScroll: true });
}

export function init() {
  rep = $("report");
  if (!rep) return;
  rep.querySelector(".rhead .x")?.addEventListener("click", closeReport);
  rep.addEventListener("close", onClose);
  // A click on the backdrop (outside the window's box) closes it too; a text
  // selection dragged out of the window does not.
  const outside = (e) => {
    if (e.target !== rep) return false;
    const b = rep.getBoundingClientRect();
    return e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom;
  };
  let downOutside = false;
  rep.addEventListener("pointerdown", (e) => { downOutside = outside(e); });
  rep.addEventListener("click", (e) => {
    if (downOutside && outside(e)) closeReport();
    downOutside = false;
  });
  // 'Dernier rapport' (design spec §3, #controlsExtra), once a report exists.
  const extra = $("controlsExtra");
  if (extra) {
    lastBtn = document.createElement("button");
    lastBtn.type = "button";
    lastBtn.id = "lastReportBtn";
    lastBtn.className = "ctl";
    lastBtn.textContent = T.controls.lastReport;
    lastBtn.setAttribute("aria-haspopup", "dialog");
    lastBtn.setAttribute("aria-controls", "report");
    lastBtn.hidden = !window.lastReport;
    lastBtn.addEventListener("click", () => { if (window.lastReport) showReport(window.lastReport); });
    extra.append(lastBtn);
  }
}
