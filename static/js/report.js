/* The report dashboard (display_report): KPI tiles, an ApexCharts chart,
   a Grid.js table and markdown notes. */
import { $, esc, md } from "./core.js";
import { T, fmtNumber } from "./strings-fr.js";

// Series palette validated for dark surfaces (CVD-safe, dataviz checks pass).
const SERIES_COLORS = ["#1e93c4", "#c47b1e", "#1ea86a"];
let _apex = null, _grid = null;
let opener = null;  // where the focus was: it goes back there on close

// Numbers the French way: '1 037', '12,5' (design spec §5).
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? fmtNumber(v) : v);

// ApexCharts writes series names, categories and values into its legend and
// tooltip as HTML: from the model, a label never carries a character that
// opens a tag or leaves an attribute, and a value is a number or nothing.
const label = (v) => String(v ?? "").replace(/</g, "‹").replace(/>/g, "›")
  .replace(/"/g, "”").replace(/'/g, "’").replace(/`/g, "ʼ");
const value = (v) => (v !== null && v !== "" && Number.isFinite(Number(v)) ? Number(v) : null);

export function showReport(r) {
  const rep = $("report");
  $("rtitle").textContent = r.title || "Rapport";

  // KPI tiles
  const kpisEl = $("kpis");
  kpisEl.innerHTML = "";
  for (const k of (r.kpis || []).slice(0, 4)) {
    const d = k.delta ? `<div class="d ${String(k.delta).trim().startsWith("-") ? "neg" : ""}">${esc(String(k.delta))}</div>` : "";
    kpisEl.insertAdjacentHTML("beforeend",
      `<div class="kpi"><div class="l">${esc(String(k.label ?? ""))}</div><div class="v">${esc(String(k.value ?? ""))}</div>${d}</div>`);
  }

  // Chart (ApexCharts)
  const chartEl = $("rchart");
  if (_apex) { _apex.destroy(); _apex = null; }
  chartEl.innerHTML = "";
  const c = r.chart;
  const series = (Array.isArray(c?.series) ? c.series : []).filter(s => s && typeof s === "object")
    .map(s => ({ name: label(s.name), data: (Array.isArray(s.data) ? s.data : []).map(value) }));
  const categories = (Array.isArray(c?.categories) ? c.categories : []).map(label);
  if (c && window.ApexCharts && series.length) {
    const donut = c.type === "donut";
    const nSeries = donut ? categories.length : series.length;
    const opts = {
      chart: { type: c.type || "line", height: 280, background: "transparent",
               toolbar: { show: false }, foreColor: "#7aa7c2",  // --muted
               fontFamily: "Rajdhani, sans-serif" },
      theme: { mode: "dark" },
      colors: SERIES_COLORS,
      stroke: { width: 2, curve: "straight" },
      grid: { borderColor: "rgba(64,220,255,.12)", strokeDashArray: 3 },
      dataLabels: { enabled: false },
      legend: { show: nSeries > 1, labels: { colors: "#cfeaff" } },
      tooltip: { theme: "dark" },
      series: donut ? series[0].data : series,
    };
    if (donut) opts.labels = categories;
    else {
      opts.xaxis = { categories };
      opts.yaxis = { labels: { formatter: num } };
    }
    opts.tooltip.y = { formatter: num };
    if (c.type === "area") opts.fill = { type: "gradient", gradient: { opacityFrom: .3, opacityTo: .02 } };
    _apex = new ApexCharts(chartEl, opts);
    _apex.render();
  }

  // Table (Grid.js)
  const tableEl = $("rtable");
  if (_grid) { _grid.destroy(); _grid = null; }
  tableEl.innerHTML = "";
  const tb = r.table;
  if (tb && window.gridjs && (tb.rows || []).length) {
    _grid = new gridjs.Grid({
      // shown in French, sorted on the raw values
      columns: (tb.columns || []).map(name => ({ name, formatter: num })),
      data: tb.rows,
      sort: true,
      search: tb.rows.length > 8,
      pagination: tb.rows.length > 12 ? { limit: 10 } : false,
      language: T.report.grid,
      style: { table: { "font-family": "Rajdhani, sans-serif" } },
    });
    _grid.render(tableEl);
  }

  // Markdown notes
  $("rmd").innerHTML = r.markdown ? md(r.markdown) : "";

  // Shown in place, like the old overlay: the HUD behind stays usable and the
  // focus stays where it was (show() would move it into the report). WP11
  // makes it a real modal window.
  if (!rep.open) opener = document.activeElement;
  rep.setAttribute("open", "");
}

export function closeReport() {
  const rep = $("report");
  if (rep.open) rep.close();
}

export function init() {
  const rep = $("report");
  rep.querySelector(".rhead .x").addEventListener("click", closeReport);
  // Closed by its ✕ or Échap: the focus goes back where it was, not to <body>.
  rep.addEventListener("close", () => {
    const back = opener;
    opener = null;
    if (rep.contains(document.activeElement) || document.activeElement === document.body) {
      if (back && back.isConnected && back !== document.body) back.focus({ preventScroll: true });
      else $("orbBtn")?.focus({ preventScroll: true });
    }
  });
}
