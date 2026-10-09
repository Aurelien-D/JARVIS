/* The report dashboard (display_report): KPI tiles, an ApexCharts chart,
   a Grid.js table and markdown notes. */
import { $, esc, md } from "./core.js";

// Series palette validated for dark surfaces (CVD-safe, dataviz checks pass).
const SERIES_COLORS = ["#1e93c4", "#c47b1e", "#1ea86a"];
let _apex = null, _grid = null;

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
  if (c && window.ApexCharts && (c.series || []).length) {
    const donut = c.type === "donut";
    const nSeries = donut ? (c.categories || []).length : c.series.length;
    const opts = {
      chart: { type: c.type || "line", height: 280, background: "transparent",
               toolbar: { show: false }, foreColor: "#4d7c99",
               fontFamily: "Rajdhani, sans-serif" },
      theme: { mode: "dark" },
      colors: SERIES_COLORS,
      stroke: { width: 2, curve: "straight" },
      grid: { borderColor: "rgba(64,220,255,.12)", strokeDashArray: 3 },
      dataLabels: { enabled: false },
      legend: { show: nSeries > 1, labels: { colors: "#cfeaff" } },
      tooltip: { theme: "dark" },
      series: donut ? c.series[0].data : c.series,
    };
    if (donut) opts.labels = c.categories || [];
    else opts.xaxis = { categories: c.categories || [] };
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
      columns: tb.columns || [],
      data: tb.rows,
      sort: true,
      search: tb.rows.length > 8,
      pagination: tb.rows.length > 12 ? { limit: 10 } : false,
      style: { table: { "font-family": "Rajdhani, sans-serif" } },
    });
    _grid.render(tableEl);
  }

  // Markdown notes
  $("rmd").innerHTML = r.markdown ? md(r.markdown) : "";

  // Shown in place, like the old overlay: the HUD behind stays usable and the
  // focus stays where it was (show() would move it into the report).
  rep.setAttribute("open", "");
}

export function closeReport() {
  const rep = $("report");
  if (rep.open) rep.close();
}

export function init() {
  $("report").querySelector(".rhead .x").addEventListener("click", closeReport);
}
