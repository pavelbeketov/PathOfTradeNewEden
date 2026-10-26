# Make_report.py
# Генерирует полный и короткий HTML-отчёты из results/analysis.json
# Запуск: py .\Make_report.py

import json
import csv
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from html import escape
import sys

from trader_common import (ROOT, DATA_DIR, atomic_text, load_settings, read_json,
                           settings_fingerprint, parse_time, utcnow)
from route_report import render_route, render_short_route


RESULTS_DIR = ROOT / "results"

JSON_PATH = RESULTS_DIR / "arbitrage_top.json"
CSV_PATH = RESULTS_DIR / "arbitrage_top.csv"
OUT_HTML = RESULTS_DIR / "arbitrage_report.html"
OUT_SHORT_HTML = RESULTS_DIR / "short_route_report.html"

def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, str) and x.strip() == "":
            return None
        return float(x)
    except Exception:
        return None


def _to_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    try:
        if isinstance(x, str) and x.strip() == "":
            return None
        return int(float(x))
    except Exception:
        return None


def load_rows() -> List[Dict[str, Any]]:
    if JSON_PATH.exists():
        rows = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("JSON должен быть массивом объектов.")
        return rows

    if CSV_PATH.exists():
        rows: List[Dict[str, Any]] = []
        with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(dict(row))
        return rows

    raise FileNotFoundError("Не найдено ни results/arbitrage_top.json, ни results/arbitrage_top.csv")


def normalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    num_float = {
        "buy_price", "sell_price_gross", "sell_price_net",
        "profit_per_unit", "profit_pct", "max_profit_est",
        "volume_m3", "mass_kg"
    }
    num_int = {
        "type_id", "buy_region_id", "buy_location_id",
        "sell_region_id", "sell_location_id",
        "buy_volume_remain", "sell_volume_remain",
        "max_units_by_orders", "max_units_by_cargo",
        "max_units_final"
    }

    out: List[Dict[str, Any]] = []
    for r in rows:
        rr = dict(r)

        for k in num_float:
            if k in rr:
                rr[k] = _to_float(rr.get(k))
        for k in num_int:
            if k in rr:
                rr[k] = _to_int(rr.get(k))

        rr["type_name"] = str(rr.get("type_name") or f"(unknown:{rr.get('type_id')})")
        rr["buy_region"] = str(rr.get("buy_region") or "")
        rr["sell_region"] = str(rr.get("sell_region") or "")

        out.append(rr)

    # дефолтная сортировка: max_profit_est desc
    out.sort(key=lambda x: (x.get("max_profit_est") or 0.0), reverse=True)
    return out


def build_html(rows: List[Dict[str, Any]], analysis=None, short_mode=False) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if short_mode:
        rows = []
    rows_json = json.dumps(rows, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    renderer = render_short_route if short_mode else render_route
    route_html = renderer(analysis) if analysis else '<p>Запусти Calculate_profit.py для построения маршрута.</p>'
    title = 'Короткий маршрут' if short_mode else 'EVE Arbitrage Report'
    route_title = 'Короткий маршрут продаж' if short_mode else 'Route loop deals'
    route_hint = 'До 6 станций · до 3 видов товара на закупку' if short_mode else 'План одного полного круга'
    subtitle = 'Простой план покупок и продаж' if short_mode else 'Показываем все позиции из файла'
    stat_text = (f'{analysis["short_plan"]["station_count"]} станций · {analysis["short_plan"]["total_jumps"]} прыжков'
                 if short_mode and analysis else '…')
    loop_current = '' if short_mode else ' aria-current="page"'
    short_current = ' aria-current="page"' if short_mode else ''
    table_html = '' if short_mode else """
    <div class="panel">
      <div class="sectionTitle">
        <h2>Bulk deals (max_units_final &gt; 1)</h2>
        <div class="hint">Сортируй кликом по колонкам: Buy/Sell, Max units, Max profit, Profit/unit</div>
      </div>
      <div class="tableWrap">
        <table id="tblBulk">
          <thead><tr id="hdrBulk"></tr></thead>
          <tbody id="bodyBulk"></tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <div class="sectionTitle">
        <h2>Single deals (max_units_final = 1)</h2>
        <div class="hint">Сортируй кликом по колонкам: Buy/Sell, Max profit</div>
      </div>
      <div class="tableWrap">
        <table id="tblSingle">
          <thead><tr id="hdrSingle"></tr></thead>
          <tbody id="bodySingle"></tbody>
        </table>
      </div>
    </div>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title}</title>

  <link rel="preconnect" href="https://cdn.jsdelivr.net" />
  <style>
    :root {{
      --bg: #070A10;
      --panel: rgba(255,255,255,0.05);
      --border: rgba(120,180,255,0.18);
      --text: rgba(235,245,255,0.92);
      --muted: rgba(235,245,255,0.60);
      --shadow: 0 10px 35px rgba(0,0,0,.45);
      --radius: 18px;

      --good: #66ff9a;
      --cyan: #4FE3FF;
      --pink: #ff6bcb;
      --warn: #ffb86b;
    }}

    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
      background:
        radial-gradient(1200px 600px at 20% 15%, rgba(79,227,255,0.10), transparent 55%),
        radial-gradient(900px 500px at 80% 25%, rgba(255,107,203,0.08), transparent 60%),
        radial-gradient(900px 700px at 50% 110%, rgba(120,180,255,0.08), transparent 55%),
        var(--bg);
      color: var(--text);
    }}

    .wrap {{
      max-width: 1500px;
      margin: 16px auto 60px;
      padding: 0 16px;
    }}

    .topbar {{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap: 12px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: rgba(0,0,0,0.20);
      box-shadow: var(--shadow);
      margin-bottom: 12px;
    }}

    .title {{
      font-weight: 900;
      letter-spacing: .3px;
      font-size: 14px;
      margin: 0;
    }}
    .sub {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }}

    .controls {{
      display:flex;
      gap: 10px;
      align-items:center;
      flex-wrap: wrap;
      justify-content:flex-end;
    }}

    .chip {{
      display:inline-flex;
      gap:8px;
      align-items:center;
      padding: 8px 10px;
      border: 1px solid rgba(120,180,255,0.18);
      border-radius: 999px;
      background: rgba(0,0,0,0.22);
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .reportNav {{ display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px; }}
    .reportNav a {{ text-decoration:none;color:var(--cyan); }}
    .reportNav a[aria-current="page"] {{ color:var(--text);background:rgba(79,227,255,.12);border-color:var(--cyan); }}

    .panel {{
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: linear-gradient(180deg, var(--panel), rgba(255,255,255,0.02));
      box-shadow: var(--shadow);
      padding: 12px;
      margin-bottom: 12px;
    }}

    .sectionTitle {{
      display:flex;
      justify-content:space-between;
      align-items:flex-end;
      gap: 12px;
      margin: 2px 0 10px 0;
    }}
    .sectionTitle h2 {{
      margin: 0;
      font-size: 14px;
      letter-spacing: .3px;
    }}
    .sectionTitle .hint {{
      color: var(--muted);
      font-size: 12px;
    }}

    /* Route panel */
    .routeGrid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(260px, 1fr));
      gap: 10px;
    }}
    @media (max-width: 1400px) {{
      .routeGrid {{ grid-template-columns: repeat(2, minmax(260px, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      .routeGrid {{ grid-template-columns: 1fr; }}
    }}
    .leg {{
      border: 1px solid rgba(120,180,255,0.14);
      border-radius: 16px;
      background: rgba(0,0,0,0.18);
      padding: 10px;
    }}
    .legTop {{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .legTitle {{
      font-weight: 900;
      font-size: 12px;
      letter-spacing: .2px;
      color: rgba(235,245,255,0.88);
    }}
    .legSub {{
      color: var(--muted);
      font-size: 11px;
      margin-top: 2px;
    }}
    .legList {{
      display:flex;
      flex-direction:column;
      gap: 6px;
      margin-top: 8px;
    }}
    .dealRow {{
      display:flex;
      justify-content:space-between;
      gap: 10px;
      padding: 8px 8px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.02);
    }}
    .dealName {{
      font-weight: 900;
      font-size: 12px;
      max-width: 220px;
      overflow:hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .dealMeta {{
      color: var(--muted);
      font-size: 11px;
      margin-top: 2px;
      white-space: nowrap;
    }}
    .dealRight {{
      text-align:right;
      white-space: nowrap;
    }}
    .dealProfit {{
      font-weight: 900;
      color: var(--good);
      font-size: 12px;
    }}
    .dealSmall {{
      color: var(--muted);
      font-size: 11px;
      margin-top: 2px;
    }}

    /* Table */
    .tableWrap {{
      overflow:auto;
      border-radius: 16px;
      border: 1px solid rgba(120,180,255,0.14);
      background: rgba(0,0,0,0.18);
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 1150px;
    }}

    thead th {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: rgba(10,14,22,0.92);
      backdrop-filter: blur(6px);
      border-bottom: 1px solid rgba(120,180,255,0.14);
      padding: 10px 10px;
      text-align: left;
      font-size: 12px;
      color: rgba(235,245,255,0.80);
      white-space: nowrap;
    }}

    thead th.sortable {{
      cursor: pointer;
      user-select: none;
    }}

    thead th .sortMark {{
      display:inline-block;
      width: 14px;
      text-align:center;
      opacity: .9;
      color: rgba(235,245,255,0.70);
      margin-left: 6px;
    }}

    tbody td {{
      border-bottom: 1px solid rgba(120,180,255,0.10);
      padding: 10px 10px;
      vertical-align: middle;
      font-size: 12px;
      color: rgba(235,245,255,0.85);
      white-space: nowrap;
    }}

    tbody tr:hover td {{
      background: rgba(79,227,255,0.06);
    }}

    .idx {{
      font-weight: 900;
      color: rgba(235,245,255,0.70);
      width: 48px;
    }}

    .name {{
      font-weight: 900;
      font-size: 12px;
      max-width: 320px;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .muted {{
      color: var(--muted);
    }}

    .iskGood {{
      color: var(--good);
      font-weight: 900;
    }}

    .pctGood {{
      color: rgba(102,255,154,0.9);
      font-weight: 900;
    }}

    .pill {{
      display:inline-flex;
      align-items:center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.10);
      font-weight: 900;
      letter-spacing: .15px;
    }}

    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      box-shadow: 0 0 0 3px rgba(255,255,255,0.08);
    }}

    .planSummary {{ display:flex;gap:12px;flex-wrap:wrap;margin:12px 0; }}
    .planMetric {{ padding:12px 16px;border:1px solid var(--border);border-radius:12px; }}
    .planMetric b {{ display:block;font-size:22px;margin-top:5px;color:var(--good); }}
    .routePath {{ font-size:16px;line-height:1.8;color:var(--cyan); }}
    .routeStep {{ margin:16px 0;padding:14px;border:1px solid var(--border);border-radius:14px; }}
    .routeStep h3 {{ margin:0 0 10px;font-size:16px; }}
    .routeAction {{ padding:12px;margin:8px 0;background:rgba(0,0,0,.2);border-radius:10px; }}
    .routeAction.completed {{ opacity:.45; }}
    .routeAction label {{ display:block;font-weight:bold;font-size:14px;cursor:pointer; }}
    .routeAction p {{ margin:8px 0;line-height:1.6; }}
    .routeAction small {{ display:block;line-height:1.7;color:var(--muted); }}
    .routeAction.buy {{ border-left:3px solid var(--cyan); }}
    .routeAction.sell {{ border-left:3px solid var(--good); }}
    .stale {{ color:var(--warn); }}
    .snapshotNote {{ line-height:1.7;font-size:13px; }}
    details {{ padding:8px 0; }} summary {{ cursor:pointer; }}
    .planAlternatives {{ min-width:600px; }}
  </style>
</head>

<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <div class="title">{title}</div>
        <div class="sub">Сгенерировано: {now} • {subtitle}</div>
      </div>
      <div class="controls">
        <span class="chip" id="statChip">{stat_text}</span>
      </div>
    </div>

    <nav class="reportNav" aria-label="Страницы результатов">
      <a class="chip" href="arbitrage_report.html"{loop_current}>Полный круг и все сделки</a>
      <a class="chip" href="short_route_report.html"{short_current}>Короткий маршрут · до 6 станций</a>
    </nav>

    <div class="panel routePlan">
      <div class="sectionTitle"><h2>{route_title}</h2><div class="hint">{route_hint}</div></div>
      {route_html}
    </div>

    {table_html}
  </div>

  <script>
    const ALL_ROWS = {rows_json};
    function esc(value) {{
      return String(value ?? "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
    }}

    function fmtISK0(x) {{
      if (x === null || x === undefined || isNaN(x)) return "n/a";
      return Number(x).toLocaleString("en-US", {{maximumFractionDigits: 0}});
    }}

    function fmtMoney2(x) {{
      if (x === null || x === undefined || isNaN(x)) return "n/a";
      return Number(x).toLocaleString("en-US", {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
    }}

    function safeNum(x, fallback=0) {{
      const n = Number(x);
      return Number.isFinite(n) ? n : fallback;
    }}

    // ----- Regions coloring (unique per region, deterministic) -----
    function hashStr(s) {{
      let h = 2166136261;
      for (let i = 0; i < s.length; i++) {{
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 16777619);
      }}
      return h >>> 0;
    }}

    function regionStyle(region) {{
      const name = String(region || "Unknown");

      // Sinq_Laison → жёлтый
      if (name === "Sinq_Laison") {{
        return {{
          bg: "rgba(255, 215, 0, 0.18)",
          br: "rgba(255, 215, 0, 0.40)",
          dot: "rgb(255, 215, 0)",
          tx: "rgba(255, 255, 255, 0.92)"
        }};
      }}

      const h = hashStr(name) % 360;
      const bg = `hsla(${{h}}, 85%, 55%, 0.14)`;
      const br = `hsla(${{h}}, 85%, 55%, 0.30)`;
      const dot = `hsl(${{h}}, 85%, 60%)`;
      const tx = `hsla(0, 0%, 100%, 0.92)`;
      return {{ bg, br, dot, tx }};
    }}

    function regionPillHTML(region) {{
      const st = regionStyle(region);
      const label = esc(region || "Unknown");
      return `
        <span class="pill" style="background:${{st.bg}};border-color:${{st.br}};color:${{st.tx}};">
          <span class="dot" style="background:${{st.dot}};"></span>
          ${{label}}
        </span>
      `;
    }}

    // ----- Stats -----
    function rowsForStats(allRows) {{
      return allRows
        .slice()
        .sort((a,b)=> safeNum(b.max_profit_est) - safeNum(a.max_profit_est));
    }}

    function computeStats(rowsAll) {{
      const n = rowsAll.length;
      let bestProfit = 0;
      let bestItem = "";
      let avgPct = 0;
      let avgUnit = 0;

      if (n > 0) {{
        bestProfit = safeNum(rowsAll[0].max_profit_est);
        bestItem = String(rowsAll[0].type_name || "");
        avgPct = rowsAll.reduce((a,r)=>a + safeNum(r.profit_pct), 0) / n;
        avgUnit = rowsAll.reduce((a,r)=>a + safeNum(r.profit_per_unit), 0) / n;
      }}

      return {{ n, bestProfit, bestItem, avgPct, avgUnit }};
    }}

    // ----- Tables / sorting -----
    const COLS_BULK = [
      {{ key: "__idx", label: "#", sortable: false }},
      {{ key: "type_name", label: "Item", sortable: false }},
      {{ key: "buy_region", label: "Buy region", sortable: false }},
      {{ key: "sell_region", label: "Sell region", sortable: false }},
      {{ key: "__stations", label: "Станции покупки → продажи", sortable: false }},
      {{ key: "__volume", label: "Объём партии, м³", sortable: false }},

      {{ key: "buy_price", label: "Buy (sell)", sortable: true }},
      {{ key: "sell_price_gross", label: "Sell (buy)", sortable: true }},
      {{ key: "max_units_final", label: "Max units", sortable: true }},
      {{ key: "max_profit_est", label: "Max profit", sortable: true }},
      {{ key: "profit_per_unit", label: "Profit / unit", sortable: true }},

      {{ key: "sell_price_net", label: "Sell net", sortable: false }},
      {{ key: "profit_pct", label: "Profit %", sortable: false }},
    ];

    const COLS_SINGLE = [
      {{ key: "__idx", label: "#", sortable: false }},
      {{ key: "type_name", label: "Item", sortable: false }},
      {{ key: "buy_region", label: "Buy region", sortable: false }},
      {{ key: "sell_region", label: "Sell region", sortable: false }},
      {{ key: "__stations", label: "Станции покупки → продажи", sortable: false }},
      {{ key: "__volume", label: "Объём партии, м³", sortable: false }},

      {{ key: "buy_price", label: "Buy (sell)", sortable: true }},
      {{ key: "sell_price_gross", label: "Sell (buy)", sortable: true }},
      {{ key: "max_profit_est", label: "Max profit", sortable: true }},

      {{ key: "sell_price_net", label: "Sell net", sortable: false }},
      {{ key: "profit_pct", label: "Profit %", sortable: false }},
    ];

    function defaultSort(a,b) {{
      return safeNum(b.max_profit_est) - safeNum(a.max_profit_est);
    }}

    function splitRows(allRows) {{
      const bulk = [];
      const single = [];
      for (const r of allRows) {{
        const u = r.max_units_final;
        const units = (u === null || u === undefined) ? 0 : Number(u);
        if (Number.isFinite(units) && units > 1) bulk.push(r);
        else single.push(r);
      }}
      return {{ bulk, single }};
    }}

    function buildHeaderRow(headerEl, cols, sortState, onSort) {{
      headerEl.innerHTML = "";
      for (const c of cols) {{
        const th = document.createElement("th");
        th.textContent = c.label;

        if (c.sortable) {{
          th.classList.add("sortable");
          const mark = document.createElement("span");
          mark.className = "sortMark";
          mark.textContent = (sortState.key === c.key) ? (sortState.dir === "asc" ? "▲" : "▼") : "↕";
          th.appendChild(mark);

          th.addEventListener("click", () => {{
            if (sortState.key === c.key) {{
              sortState.dir = (sortState.dir === "asc") ? "desc" : "asc";
            }} else {{
              sortState.key = c.key;
              sortState.dir = "desc";
            }}
            onSort();
          }});
        }}
        headerEl.appendChild(th);
      }}
    }}

    function getSortedRows(rows, sortState) {{
      const out = rows.slice();
      if (!sortState.key) {{
        out.sort(defaultSort);
        return out;
      }}
      const k = sortState.key;
      const dir = sortState.dir === "asc" ? 1 : -1;

      out.sort((a,b) => {{
        if (["buy_price","sell_price_gross","max_units_final","max_profit_est","profit_per_unit"].includes(k)) {{
          return dir * (safeNum(a[k]) - safeNum(b[k]));
        }}

        const av = String(a[k] ?? "");
        const bv = String(b[k] ?? "");
        if (av < bv) return -1 * dir;
        if (av > bv) return  1 * dir;
        return 0;
      }});

      return out;
    }}

    function rowToTR(r, idx, cols) {{
      const tr = document.createElement("tr");

      const cells = {{
        "__idx": () => `<span class="idx">${{idx}}</span>`,
        "type_name": () => `
          <div class="name" title="${{esc(r.type_name||"")}}">${{esc(r.type_name||"")}}</div>
          <div class="muted" style="font-size:11px;">type_id: ${{esc(r.type_id)}}</div>
        `,
        "buy_region": () => regionPillHTML(r.buy_region),
        "sell_region": () => regionPillHTML(r.sell_region),

        "__stations": () => `<div>${{esc(r.buy_station || r.buy_location_id)}}</div><div class="muted">→ ${{esc(r.sell_station || r.sell_location_id)}}</div>`,
        "__volume": () => fmtMoney2(safeNum(r.volume_m3) * safeNum(r.max_units_final)),
        "buy_price": () => `${{fmtMoney2(r.buy_price)}} ISK`,
        "sell_price_gross": () => `${{fmtMoney2(r.sell_price_gross)}} ISK`,
        "sell_price_net": () => `${{fmtMoney2(r.sell_price_net)}} ISK`,

        "profit_per_unit": () => `<span class="iskGood">${{fmtMoney2(r.profit_per_unit)}} ISK</span>`,
        "profit_pct": () => {{
          const v = (r.profit_pct === null || r.profit_pct === undefined) ? null : Number(r.profit_pct);
          if (v === null || !Number.isFinite(v)) return "n/a";
          return `<span class="pctGood">${{v.toFixed(2)}}%</span>`;
        }},
        "max_units_final": () => String(r.max_units_final ?? "n/a"),
        "max_profit_est": () => `<span class="iskGood">${{fmtISK0(r.max_profit_est)}} ISK</span>`,
      }};

      for (const c of cols) {{
        const td = document.createElement("td");
        td.innerHTML = (cells[c.key] ? cells[c.key]() : esc(r[c.key] ?? ""));
        tr.appendChild(td);
      }}

      return tr;
    }}

    function renderTable(bodyEl, rows, cols, sortState) {{
      bodyEl.innerHTML = "";
      const sorted = getSortedRows(rows, sortState);

      sorted.forEach((r, i) => {{
        bodyEl.appendChild(rowToTR(r, i + 1, cols));
      }});
    }}

    // ----- State -----
    const sortBulk = {{ key: "max_profit_est", dir: "desc" }};
    const sortSingle = {{ key: "max_profit_est", dir: "desc" }};

    function refresh() {{
      if (!document.getElementById("tblBulk")) return;
      const allSorted = rowsForStats(ALL_ROWS);
      const st = computeStats(allSorted);

      document.getElementById("statChip").textContent =
        `Total: ${{st.n}} • Best: ${{fmtISK0(st.bestProfit)}} ISK • Avg: ${{fmtISK0(st.avgUnit)}} ISK/unit • ${{st.avgPct.toFixed(2)}}%`;



      const parts = splitRows(ALL_ROWS);

      buildHeaderRow(document.getElementById("hdrBulk"), COLS_BULK, sortBulk, refresh);
      buildHeaderRow(document.getElementById("hdrSingle"), COLS_SINGLE, sortSingle, refresh);

      renderTable(document.getElementById("bodyBulk"), parts.bulk, COLS_BULK, sortBulk);
      renderTable(document.getElementById("bodySingle"), parts.single, COLS_SINGLE, sortSingle);
    }}

    refresh();
    document.querySelectorAll(".actionCheck").forEach(box => {{
      const key = box.dataset.key;
      try {{ box.checked = localStorage.getItem(key) === "done"; }} catch (_) {{}}
      box.addEventListener("change", () => {{
        try {{ localStorage.setItem(key, box.checked ? "done" : ""); }} catch (_) {{}}
        box.closest(".routeAction").classList.toggle("completed", box.checked);
      }});
      box.closest(".routeAction").classList.toggle("completed", box.checked);
    }});
    const ageEl = document.getElementById("snapshotAge");
    function updateAge() {{
      if (!ageEl) return;
      const minutes = Math.max(0, (Date.now() - Date.parse(ageEl.dataset.time)) / 60000);
      ageEl.textContent = `Возраст снимка: ${{minutes.toFixed(0)}} мин. Перед покупкой проверь цену и количество в игре.`;
      ageEl.classList.toggle("stale", minutes > Number(ageEl.dataset.max));
    }}
    updateAge(); setInterval(updateAge, 60000);
  </script>
</body>
</html>
"""


def main() -> None:
    settings = load_settings()
    analysis = read_json(RESULTS_DIR / "analysis.json")
    manifest = read_json(DATA_DIR / "scan_manifest.json")
    if (analysis.get("schema_version") != 2 or not isinstance(analysis.get("short_plan"), dict)
            or analysis.get("settings_hash") != settings_fingerprint(settings)):
        raise ValueError("Настройки изменились или старый формат результатов: повтори Calculate_profit.py")
    if manifest.get("success") is not True or analysis.get("snapshot_id") != manifest.get("snapshot_id"):
        raise ValueError("Снимок изменился или сканирование неуспешно: повтори Order → Calculate")
    for times in analysis['snapshot_times'].values():
        age = (utcnow() - parse_time(times['checked_at_utc'])).total_seconds()
        if not -60 <= age <= settings['max_snapshot_age_minutes'] * 60:
            raise ValueError("Результаты устарели. Повтори Order → Calculate → Make")
    rows = normalize(analysis['rows'])
    full_html = build_html(rows, analysis)
    short_html = build_html([], analysis, short_mode=True)
    atomic_text(OUT_HTML, full_html)
    atomic_text(OUT_SHORT_HTML, short_html)
    print(f"Saved: {OUT_HTML.resolve()}")
    print(f"Saved: {OUT_SHORT_HTML.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"REPORT FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
