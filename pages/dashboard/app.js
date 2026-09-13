/* SoL-Astr 面板。请求全部走 bridge —— 页面在 iframe 里，自己 fetch 拿不到 JWT。 */

const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

const MODE_TEXT = { off: "未启用", dry_run: "影子模式", live: "压缩生效中" };
const OUTCOME_TEXT = {
  not_called: "够格未调用",
  too_small: "归档段太小",
  dry_run: "影子记录",
  empty_summary: "摘要为空",
  compacted: "已压缩",
};
const OUTCOME_COLOR = {
  not_called: "var(--text-3)",
  too_small: "#9ca3af",
  dry_run: "var(--warn)",
  empty_summary: "var(--danger)",
  compacted: "var(--accent)",
};

const fmt = (n) => (n ?? 0).toLocaleString("en-US");
const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n));
const money = (n) => `$${n < 0.01 ? n.toFixed(5) : n.toFixed(3)}`;

let state = {};

/* ---------------- 渲染：关键数字与告警 ---------------- */

function renderHero(status, ledger) {
  const t = status.thresholds;
  $("mode-badge").dataset.mode = status.mode;
  $("mode-badge").lastElementChild.textContent = MODE_TEXT[status.mode] || status.mode;

  $("hero-sub").textContent =
    status.mode === "off"
      ? "插件当前未启用。在下方配置里打开总开关后，模型才会看到压缩工具。"
      : status.mode === "dry_run"
        ? "影子模式：全流程照跑，但不调用摘要模型、不改写任何历史，只记台账。"
        : `压缩已生效。上下文超过 ${fmt(t.threshold)} tokens 后，模型可在话题边界自行压缩。`;

  const rate = ledger.eligible ? `${(ledger.call_rate * 100).toFixed(0)}%` : "—";
  $("kpis").innerHTML = [
    ["压缩线", fmtK(t.threshold), "tokens", `保留 ${fmtK(t.keep_recent)} + 归档 ${fmtK(t.min_archive)}`],
    ["模型上限", fmtK(t.window), "tokens", `来源：${t.window_source}`],
    ["触发率", rate, "", `${ledger.counts.not_called} 次够格未调用`],
    ["已压缩", String(ledger.counts.compacted), "次", ledger.duration_ms_avg ? `平均耗时 ${(ledger.duration_ms_avg / 1000).toFixed(1)}s` : "尚未发生"],
  ]
    .map(
      ([label, value, unit, sub]) => `
      <div class="kpi">
        <div class="label">${label}</div>
        <div class="value num">${value}${unit ? `<span class="unit">${unit}</span>` : ""}</div>
        <div class="sub">${sub}</div>
      </div>`,
    )
    .join("");

  const icon = `<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
    <circle cx="10" cy="10" r="8"/><path d="M10 6v5M10 13.6v.4" stroke-linecap="round"/></svg>`;
  $("alerts").innerHTML = (status.alerts || [])
    .map(
      (a) => `<div class="alert" data-level="${a.level}">${icon}
        <div><h4>${a.title}</h4><p>${a.body}</p><div class="fix">${a.fix}</div></div>
      </div>`,
    )
    .join("");
}

/* ---------------- 渲染：上下文标尺 ---------------- */

function renderRuler(sessions) {
  const { window: win, threshold, keep_recent, builtin_fallback, sessions: rows } = sessions;
  const W = 1000;
  const H = 108;
  const y = 62;
  const x = (v) => Math.max(0, Math.min(1, v / win)) * W;

  const marks = [
    { v: keep_recent, label: `保留段 ${fmtK(keep_recent)}`, color: "var(--text-3)" },
    { v: threshold, label: `压缩线 ${fmtK(threshold)}`, color: "var(--accent)" },
    { v: builtin_fallback, label: `内置兜底 ${fmtK(builtin_fallback)}`, color: "var(--warn)" },
  ];

  const bands = `
    <rect x="0" y="${y - 7}" width="${x(keep_recent)}" height="14" rx="7" fill="var(--surface-2)"/>
    <rect x="${x(keep_recent)}" y="${y - 7}" width="${x(threshold) - x(keep_recent)}" height="14" fill="var(--surface-2)"/>
    <rect x="${x(threshold)}" y="${y - 7}" width="${x(builtin_fallback) - x(threshold)}" height="14" fill="var(--accent-soft)"/>
    <rect x="${x(builtin_fallback)}" y="${y - 7}" width="${W - x(builtin_fallback)}" height="14" rx="7" fill="var(--warn-bg)"/>`;

  const ticks = marks
    .map(
      (m) => `<g>
        <line x1="${x(m.v)}" y1="${y - 18}" x2="${x(m.v)}" y2="${y + 18}" stroke="${m.color}" stroke-width="1.5"/>
        <text x="${x(m.v)}" y="${y - 26}" fill="${m.color}" font-size="12.5" text-anchor="middle">${m.label}</text>
      </g>`,
    )
    .join("");

  const cursors = rows
    .slice(0, 16)
    .map((s) => {
      const cx = x(s.tokens);
      return `<g>
        <circle cx="${cx}" cy="${y}" r="6.5" fill="var(--surface)" stroke="var(--accent)" stroke-width="2.5"/>
        <title>${s.title} · ${fmt(s.tokens)} tokens</title>
      </g>`;
    })
    .join("");

  $("ruler").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">
    ${bands}${ticks}${cursors}
    <text x="0" y="${y + 34}" fill="var(--text-3)" font-size="12">0</text>
    <text x="${W}" y="${y + 34}" fill="var(--text-3)" font-size="12" text-anchor="end">${fmtK(win)}</text>
  </svg>`;

  const over = rows.filter((s) => s.tokens >= threshold).length;
  $("ruler-legend").innerHTML = [
    ["var(--surface-2)", "保留段", "压缩后原样留下的最近对话"],
    ["var(--accent-soft)", "可压缩区", `${over} 个会话已进入`],
    ["var(--warn-bg)", "内置兜底区", "到这里核心会自己动手压"],
  ]
    .map(
      ([c, k, v]) =>
        `<div class="legend-item"><span class="swatch" style="background:${c}"></span><b>${k}</b> · ${v}</div>`,
    )
    .join("");

  $("sessions-card").innerHTML = rows.length
    ? `<table><thead><tr><th>会话</th><th class="right">当前用量</th><th class="right">距压缩线</th></tr></thead>
       <tbody>${rows
         .slice(0, 12)
         .map((s) => {
           const gap = threshold - s.tokens;
           return `<tr><td>${s.title}</td>
             <td class="right num">${fmt(s.tokens)}</td>
             <td class="right num" style="color:${gap <= 0 ? "var(--accent)" : "var(--text-3)"}">
               ${gap <= 0 ? "已过线" : `还差 ${fmt(gap)}`}</td></tr>`;
         })
         .join("")}</tbody></table>`
    : `<div class="empty">还没有产生 token 用量的会话。聊几轮之后这里会出现数据。</div>`;
}

/* ---------------- 渲染：模型与价目 ---------------- */

function priceGrid(m) {
  if (!m.resolved) {
    return `<div class="alias">目录里没认出这个名字，价目不可用。</div>`;
  }
  const p = m.price;
  return `<div class="price-grid">
    ${[
      ["输入", money(p.input)],
      ["输出", money(p.output)],
      [p.has_cache_discount ? "缓存读" : "缓存读（无折扣）", money(p.cache_read_effective)],
      ["缓存写", p.cache_write ? money(p.cache_write) : "不收费"],
    ]
      .map(([k, v]) => `<div class="price-cell"><div class="k">${k}</div><div class="v num">${v}</div></div>`)
      .join("")}
  </div>`;
}

function modelCard(m) {
  return `<div class="model-card">
    <div class="role">${m.role}</div>
    <div class="mid">${m.resolved ? m.id : m.configured_name || "未配置"}</div>
    <div class="alias">${
      m.resolved
        ? `配置名 ${m.configured_name} · 命中 ${m.hits.join(" / ") || "—"}${
            m.muted.length ? ` · 忽略 ${m.muted.join(" / ")}` : ""
          }`
        : ""
    }</div>
    ${priceGrid(m)}
  </div>`;
}

function renderModels(models, status) {
  if (models.catalog_error && !models.catalog_size) {
    $("models-card").innerHTML =
      `<div class="empty">拿不到 OpenRouter 价目：${models.catalog_error}</div>`;
    return;
  }
  const spot = status.sweet_spot;
  const arrow = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
    <path d="M5 12h14M13 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  let verdict = `<div class="verdict">价目齐全，但还没算出结论。</div>`;
  if (spot && !spot.solvable) {
    verdict = `<div class="verdict" data-bad="1"><b>这套组合永远不回本。</b><br/>${spot.reason}</div>`;
  } else if (spot) {
    verdict = `<div class="verdict">
      按现在的价目，压一次的一次性成本是 <b class="num">${money(spot.one_time_cost)}</b>，
      要求 <b>${spot.breakeven_turns}</b> 次请求内回本，反推出归档段至少
      <b class="num">${fmt(spot.min_archive)}</b> tokens —— 这就是压缩线
      <b class="num">${fmt(status.thresholds.threshold)}</b> 的由来。</div>`;
  }

  $("models-card").innerHTML =
    `<div class="models">${modelCard(models.main)}<div class="arrow">${arrow}</div>${modelCard(models.summarizer)}</div>${verdict}
     <div class="alias" style="margin-top:14px;color:var(--text-3);font-size:12.5px">
       目录共 ${models.catalog_size} 个模型。价目为官方牌价（$/1M tokens），
       走聚合渠道时实付可能不同，此处仅用于比较划算程度。</div>`;
}

/* ---------------- 渲染：模拟器 ---------------- */

function computeSpot(main, summ, keep, turns, memo) {
  const pi = main.price.input / 1e6;
  const pc = main.price.cache_read_effective / 1e6;
  const pw = main.price.cache_write / 1e6;
  const si = summ.price.input / 1e6;
  const so = summ.price.output / 1e6;
  const once = (keep + memo) * (pi - pc + pw) + memo * so;
  const den = turns * pc - si;
  if (den <= 0) return { solvable: false, once };
  const archive = Math.round(once / den);
  return { solvable: true, once, archive, threshold: keep + archive };
}

function renderSim(models) {
  if (!models.main.resolved || !models.summarizer.resolved) {
    $("sim-card").innerHTML = `<div class="empty">两个模型都解析到价目后才能模拟。</div>`;
    return;
  }
  const memo = models.memo_tokens;
  $("sim-card").innerHTML = `
    <div class="sim">
      <div>
        <div class="slider-row">
          <div class="head"><span class="k">目标回本轮数</span><span class="v num" id="sim-n-v"></span></div>
          <input type="range" id="sim-n" min="1" max="20" step="1" />
        </div>
        <div class="slider-row">
          <div class="head"><span class="k">保留段</span><span class="v num" id="sim-k-v"></span></div>
          <input type="range" id="sim-k" min="4000" max="80000" step="2000" />
        </div>
        <button class="primary-btn" id="sim-apply">应用到配置</button>
      </div>
      <div class="sim-out" id="sim-out"></div>
    </div>`;

  const nEl = $("sim-n");
  const kEl = $("sim-k");
  nEl.value = state.status.config.target_turns;
  kEl.value = state.status.thresholds.keep_recent;

  const update = () => {
    const n = +nEl.value;
    const k = +kEl.value;
    $("sim-n-v").textContent = n;
    $("sim-k-v").textContent = fmt(k);
    const r = computeSpot(models.main, models.summarizer, k, n, memo);
    $("sim-out").innerHTML = r.solvable
      ? `
      <div class="sim-line"><span class="k">压缩线</span><span class="v num">${fmt(r.threshold)}</span></div>
      <div class="sim-line"><span class="k">最小归档段</span><span class="v num">${fmt(r.archive)}</span></div>
      <div class="sim-line"><span class="k">一次性成本</span><span class="v num">${money(r.once)}</span></div>
      <div class="sim-line" style="border:none"><span class="k">占模型窗口</span>
        <span class="v num">${((r.threshold / state.status.thresholds.window) * 100).toFixed(0)}%</span></div>`
      : `<div class="verdict" data-bad="1">这个回本轮数下无解：摘要模型太贵，省下的还不够付摘要钱。把轮数调大，或换更便宜的压缩模型。</div>`;
  };

  nEl.addEventListener("input", update);
  kEl.addEventListener("input", update);
  update();

  $("sim-apply").addEventListener("click", async () => {
    const btn = $("sim-apply");
    btn.disabled = true;
    btn.textContent = "保存中…";
    await bridge.apiPost("config", {
      target_turns: +nEl.value,
      keep_recent_tokens: +kEl.value,
    });
    btn.textContent = "已应用";
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "应用到配置";
      load();
    }, 700);
  });
}

/* ---------------- 渲染：台账 ---------------- */

function renderLedger(l) {
  if (!l.total) {
    $("ledger-card").innerHTML = `<div class="empty">台账还是空的。插件启用后每次工具调用都会记一条。</div>`;
    return;
  }
  const dist = Object.entries(l.counts)
    .filter(([, v]) => v > 0)
    .map(
      ([k, v]) =>
        `<span style="flex-grow:${v};background:${OUTCOME_COLOR[k]}" title="${OUTCOME_TEXT[k]} ${v}"></span>`,
    )
    .join("");

  const legend = Object.entries(l.counts)
    .filter(([, v]) => v > 0)
    .map(
      ([k, v]) =>
        `<div class="legend-item"><span class="swatch" style="background:${OUTCOME_COLOR[k]}"></span><b>${OUTCOME_TEXT[k]}</b> · ${v}</div>`,
    )
    .join("");

  const a = l.archive_tokens;
  let hist = "";
  if (a.length) {
    const max = Math.max(...a);
    const buckets = new Array(18).fill(0);
    a.forEach((v) => buckets[Math.min(17, Math.floor((v / (max || 1)) * 17))]++);
    const top = Math.max(...buckets, 1);
    hist = `<div style="margin-top:26px">
      <div class="k" style="font-size:12.5px;color:var(--text-3)">归档段大小分布（最大 ${fmtK(max)}）</div>
      <div class="hist">${buckets.map((b) => `<div class="bar" style="height:${(b / top) * 100}%"></div>`).join("")}</div>
    </div>`;
  }

  const compacted = l.compacted.length
    ? `<table style="margin-top:26px"><thead><tr>
        <th>压缩前</th><th>压缩后</th><th class="right">归档</th><th class="right">摘要</th><th class="right">耗时</th>
      </tr></thead><tbody>${l.compacted
        .slice(-8)
        .reverse()
        .map(
          (c) => `<tr>
            <td class="num">${fmt(c.before)}</td>
            <td class="num" style="color:var(--accent)">${fmt(c.after)}</td>
            <td class="right num">${fmt(c.archive)}</td>
            <td class="right num">${fmt(c.memo)}</td>
            <td class="right num">${(c.duration_ms / 1000).toFixed(1)}s</td></tr>`,
        )
        .join("")}</tbody></table>`
    : "";

  $("ledger-card").innerHTML = `<div class="dist">${dist}</div>
    <div class="legend" style="border:none;padding-top:0;margin-top:14px">${legend}</div>
    ${hist}${compacted}`;
}

/* ---------------- 渲染：配置 ---------------- */

const FIELDS = [
  ["enable_compact", "bool", "启用 SoL-Astr", "关掉后模型看不到压缩工具，插件完全不介入。"],
  ["dry_run", "bool", "影子模式", "只记台账，不调摘要模型、不改写历史。先用它观察触发率。"],
  ["keep_recent_tokens", "int", "保留段 tokens", "填 0 表示按模型窗口自动算（窗口的 15%，最多 40000）。"],
  ["min_archive_tokens", "int", "最小归档段 tokens", "填 0 表示按价目自动算；价目不可用时退回窗口的 25%。"],
  ["target_turns", "int", "目标回本轮数", "压缩这笔开销要求在几次 LLM 请求内赚回来。越大压得越早。"],
  ["strip_tool_trace", "bool", "剥离工具调用痕迹", "压缩成功那一轮，落盘前删掉工具调用记录，免得模型照着复读。"],
];

function renderConfig(status) {
  const c = status.config;
  const core = status.core;
  $("config-card").innerHTML =
    FIELDS.map(
      ([key, type, label, hint]) => `
      <div class="form-row">
        <div class="desc"><div class="k">${label}</div><div class="h">${hint}</div></div>
        ${
          type === "bool"
            ? `<label class="switch"><input type="checkbox" data-key="${key}" ${c[key] ? "checked" : ""}/><span class="track"></span></label>`
            : `<input type="number" data-key="${key}" value="${c[key]}" />`
        }
      </div>`,
    ).join("") +
    `<button class="primary-btn" id="save-config">保存</button>
     <div class="sec-head" style="margin:44px 0 0">
       <h2 style="font-size:19px">核心配置（只读）</h2>
       <p style="font-size:13.5px">这些在「上下文管理策略」里改，插件不代改。</p>
     </div>
     <div class="readonly-grid">
       ${[
         ["压缩模型", core.provider_id || "未配置，回落当前聊天模型"],
         ["压缩提示词来源", core.instruction_source],
         ["按轮截断 max_turns", core.max_turns],
         ["溢出策略", core.overflow_strategy || "—"],
         ["内置保留比例", core.keep_recent_ratio],
         ["窗口兜底值", fmt(core.fallback_max_tokens)],
       ]
         .map(([k, v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${v}</div></div>`)
         .join("")}
     </div>
     <details><summary>查看发给摘要模型的完整 prompt</summary><pre>${
       status.prompt_preview.replace(/</g, "&lt;")
     }</pre></details>`;

  $("save-config").addEventListener("click", async () => {
    const btn = $("save-config");
    const body = {};
    $("config-card")
      .querySelectorAll("[data-key]")
      .forEach((el) => {
        body[el.dataset.key] = el.type === "checkbox" ? el.checked : +el.value;
      });
    btn.disabled = true;
    btn.textContent = "保存中…";
    await bridge.apiPost("config", body);
    btn.textContent = "已保存";
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "保存";
      load();
    }, 700);
  });
}

/* ---------------- 加载 ---------------- */

async function load() {
  const [status, models, sessions, ledger] = await Promise.all([
    bridge.apiGet("status"),
    bridge.apiGet("models"),
    bridge.apiGet("sessions"),
    bridge.apiGet("ledger"),
  ]);
  state = { status, models, sessions, ledger };
  renderHero(status, ledger);
  renderRuler(sessions);
  renderModels(models, status);
  renderSim(models);
  renderLedger(ledger);
  renderConfig(status);
}

await bridge.ready();
$("refresh").addEventListener("click", load);
await load();
