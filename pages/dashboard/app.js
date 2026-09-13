/* SoL-Astr 面板。请求全部走 bridge —— 页面在 iframe 里，自己 fetch 拿不到 JWT。 */

const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

const MODE_TEXT = { off: "未启用", dry_run: "影子模式", live: "生效中" };
const OUTCOME_TEXT = {
  not_called: "够格没调",
  too_small: "归档段太小",
  dry_run: "影子记录",
  empty_summary: "摘要为空",
  compacted: "已压缩",
};
const OUTCOME_COLOR = {
  not_called: "var(--ink-3)",
  too_small: "#a1a1aa",
  dry_run: "var(--warn)",
  empty_summary: "var(--danger)",
  compacted: "var(--accent)",
};

const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
const fmt = (n) => (n ?? 0).toLocaleString("en-US");
const fmtK = (n) =>
  n >= 1e6
    ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
    : n >= 1000
      ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k`
      : String(n);
const money = (n) => `$${n < 0.01 ? n.toFixed(5) : n.toFixed(3)}`;

let state = {};

/* ---------------- 状态读出 ---------------- */

function renderStatus(status, ledger) {
  const t = status.thresholds;
  const tag = $("mode-tag");
  tag.dataset.mode = status.mode;
  tag.textContent = MODE_TEXT[status.mode] || status.mode;

  $("readout").textContent =
    status.mode === "off"
      ? "插件没开。到下面配置里打开总开关，模型才看得见压缩工具。"
      : status.mode === "dry_run"
        ? "影子模式：流程照跑，不动历史，只记账。"
        : `生效中。上下文过 ${fmt(t.threshold)} 就建议模型压缩。`;

  const rate = ledger.eligible ? `${(ledger.call_rate * 100).toFixed(0)}%` : "—";
  const cells = [
    ["压缩线", fmtK(t.threshold), "tokens", `窗口的 ${((t.threshold / t.window) * 100).toFixed(0)}% · 保留 ${fmtK(t.keep_recent)}`],
    ["窗口", fmtK(t.window), "tokens", `来源 ${esc(t.window_source)}`],
    ["触发率", rate, "", ledger.eligible ? `${ledger.counts.not_called} 次够格没调` : "还没数据"],
    [
      "已压缩",
      String(ledger.counts.compacted),
      "次",
      ledger.duration_ms_avg ? `平均 ${(ledger.duration_ms_avg / 1000).toFixed(1)}s` : "没发生过",
    ],
  ];
  $("stats").innerHTML = cells
    .map(
      ([k, v, u, s]) => `<div>
        <dt>${k}</dt>
        <dd class="v">${v}${u ? `<span class="u">${u}</span>` : ""}</dd>
        <dd class="s">${s}</dd>
      </div>`,
    )
    .join("");

  $("alerts").innerHTML = (status.alerts || [])
    .map(
      (a) => `<div class="alert" data-level="${esc(a.level)}">
        <span class="lv">${a.level === "error" ? "错误" : "告警"}</span><h4>${esc(a.title)}</h4>
        <p>${esc(a.body)}</p>
        <div class="fix">${esc(a.fix)}</div>
      </div>`,
    )
    .join("");
}

/* ---------------- 标尺 ---------------- */

/* 对数轴：窗口上到 1M 时线性轴会把所有信息挤死在左边。轴从 10^3 开始。 */

function logScale(win) {
  const lo = 3;
  const hi = Math.log10(Math.max(win, 10 ** (lo + 1)));
  return (v) =>
    Math.max(0, Math.min(1, (Math.log10(Math.max(v, 10 ** lo)) - lo) / (hi - lo)));
}

function renderRuler(sessions, source) {
  const { window: win, threshold, keep_recent, builtin_fallback, sessions: rows } = sessions;
  const W = 1000;
  const H = 120;
  const y = 54;
  const u = logScale(win);
  const x = (v) => u(v) * W;

  $("ruler-note").textContent = `对数轴 · 窗口 ${fmtK(win)}，来源 ${source}`;

  const grid = [];
  for (let p = 3; p <= Math.floor(Math.log10(win)); p++) {
    const gx = x(10 ** p);
    const anchor = gx > W - 30 ? "end" : gx < 30 ? "start" : "middle";
    const label = p < 6 ? `${10 ** (p - 3)}k` : `${10 ** (p - 6)}M`;
    grid.push(`<line x1="${gx}" y1="${y - 24}" x2="${gx}" y2="${y + 26}" stroke="var(--line)" stroke-width="1"/>
      <text x="${gx}" y="${y + 42}" fill="var(--ink-3)" font-size="10" font-family="var(--mono)" text-anchor="${anchor}">${label}</text>`);
  }

  const zones = `
    <rect x="0" y="${y - 8}" width="${x(keep_recent)}" height="16" fill="var(--ink)" opacity="0.08"/>
    <rect x="${x(keep_recent)}" y="${y - 8}" width="${x(threshold) - x(keep_recent)}" height="16" fill="var(--ink)" opacity="0.03"/>
    <rect x="${x(threshold)}" y="${y - 8}" width="${Math.max(0, x(builtin_fallback) - x(threshold))}" height="16" fill="var(--accent-soft)"/>
    <rect x="${x(builtin_fallback)}" y="${y - 8}" width="${W - x(builtin_fallback)}" height="16" fill="url(#hatch)"/>
    <rect x="${x(builtin_fallback)}" y="${y - 8}" width="${W - x(builtin_fallback)}" height="16" fill="var(--warn-bg)"/>
    <line x1="0" y1="${y + 8}" x2="${W}" y2="${y + 8}" stroke="var(--line)" stroke-width="1"/>`;

  const tick = (v, label, color, above) => `
    <line x1="${x(v)}" y1="${above ? y - 22 : y + 8}" x2="${x(v)}" y2="${above ? y - 8 : y + 20}" stroke="${color}" stroke-width="1.5"/>
    <text x="${x(v)}" y="${above ? y - 30 : y + 34}" fill="${color}" font-size="11.5"
      font-family="var(--mono)" text-anchor="middle">${label}</text>`;

  const ticks =
    tick(threshold, `压缩线 ${fmtK(threshold)}`, "var(--accent)", true) +
    tick(keep_recent, `保留 ${fmtK(keep_recent)}`, "var(--ink-3)", false) +
    tick(builtin_fallback, `兜底 ${fmtK(builtin_fallback)}`, "var(--warn)", false);

  const rugs = rows
    .map((s) => {
      const over = s.tokens >= threshold;
      return `<line x1="${x(s.tokens)}" y1="${y + 10}" x2="${x(s.tokens)}" y2="${y + 26}"
        stroke="${over ? "var(--accent)" : "var(--ink-3)"}" stroke-width="2"
        opacity="${over ? 0.95 : 0.4}" stroke-linecap="round">
        <title>${esc(s.title)} · ${esc(s.user_id)} · ${fmt(s.tokens)} tokens</title></line>`;
    })
    .join("");

  $("ruler").innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="height:${H}px">
    <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="6" stroke="var(--warn)" stroke-width="1" opacity="0.4"/>
    </pattern></defs>
    ${grid.join("")}${zones}${ticks}${rugs}
  </svg>`;

  const over = rows.filter((s) => s.tokens >= threshold).length;
  $("ruler-legend").innerHTML =
    [
      ["rgba(128,128,128,0.15)", "保留段", "压缩后原样留下的最近对话"],
      ["var(--accent-soft)", "可压缩区", `${over} 个会话已过线`],
      ["var(--warn-bg)", "内置兜底区", "到这里核心自己动手"],
    ]
      .map(
        ([c, k, v]) =>
          `<div class="legend-item"><span class="swatch" style="background:${c}"></span><b>${k}</b> ${v}</div>`,
      )
      .join("") +
    `<div class="legend-item">竖线 = 会话用量，紫色已过线</div>`;
}

/* ---------------- 会话 ---------------- */

function renderSessions(sessions) {
  const { threshold, sessions: rows } = sessions;
  $("sessions").innerHTML = rows.length
    ? `<table><thead><tr><th>会话</th><th class="right">用量</th><th class="right">距压缩线</th></tr></thead>
       <tbody>${rows
         .slice(0, 12)
         .map((s) => {
           const gap = threshold - s.tokens;
           const named = s.title && s.title !== "未命名会话";
           return `<tr><td>${esc(named ? s.title : s.user_id)}${
             named ? `<div class="umo">${esc(s.user_id)}</div>` : ""
           }</td>
             <td class="right num">${fmt(s.tokens)}</td>
             <td class="right num" style="color:${gap <= 0 ? "var(--accent)" : "var(--ink-3)"}">
               ${gap <= 0 ? "已过线" : `还差 ${fmt(gap)}`}</td></tr>`;
         })
         .join("")}</tbody></table>`
    : `<div class="empty">还没有会话用量，聊几轮再来。</div>`;
}

/* ---------------- 价目 ---------------- */

function priceRows(m) {
  if (!m.resolved) {
    return `<div class="alias" style="margin-top:14px">目录里没认出这个名字，价目不可用。</div>`;
  }
  const p = m.price;
  return `<div class="prices">
    ${[
      ["输入", money(p.input)],
      ["输出", money(p.output)],
      [p.has_cache_discount ? "缓存读" : "缓存读（无折扣）", money(p.cache_read_effective)],
      ["缓存写", p.cache_write ? money(p.cache_write) : "不收费"],
    ]
      .map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`)
      .join("")}
  </div>`;
}

function modelBlock(m) {
  return `<div class="model">
    <div class="role">${esc(m.role)}</div>
    <div class="mid">${esc(m.resolved ? m.id : m.configured_name || "未配置")}</div>
    <div class="alias">${
      m.resolved
        ? `配置 ${esc(m.configured_name)} · 命中 ${esc(m.hits.join(" / ") || "—")}${
            m.muted.length ? ` · 忽略 ${esc(m.muted.join(" / "))}` : ""
          }`
        : ""
    }</div>
    ${priceRows(m)}
  </div>`;
}

function renderModels(models, status) {
  if (models.catalog_error && !models.catalog_size) {
    $("models").innerHTML = `<div class="empty">拿不到 OpenRouter 价目：${esc(models.catalog_error)}</div>`;
    return;
  }
  const spot = status.sweet_spot;
  let verdict = `<div class="verdict">价目齐全，还没算出结论。</div>`;
  if (spot && !spot.solvable) {
    verdict = `<div class="verdict" data-bad="1">${esc(spot.reason)}</div>`;
  } else if (spot) {
    verdict = `<div class="verdict">在这条线上压一次花 <span class="num">${money(spot.one_time_cost)}</span>，
      之后每轮少花 <span class="num">${money(spot.per_round_saving)}</span>。
      聊到第 <b>${spot.breakeven_turns.toFixed(1)}</b> 轮两条成本线打平，
      按预期的 ${spot.detail.target_turns} 轮算，净省 <span class="num">${money(spot.net_saving)}</span>。</div>`;
  }

  $("models").innerHTML =
    `<div class="models">${modelBlock(models.main)}<div class="arrow">-&gt;</div>${modelBlock(models.summarizer)}</div>${verdict}
     <div class="footnote">目录共 ${models.catalog_size} 个模型，单位 $/1M tokens。走聚合渠道实付可能不同，这里只比谁划算。</div>`;
}

/* ---------------- 模拟 ---------------- */

function computeEval(main, summ, keep, archive, memo) {
  const pi = main.price.input / 1e6;
  const pc = main.price.cache_read_effective / 1e6;
  const pw = main.price.cache_write / 1e6;
  const si = summ.price.input / 1e6;
  const so = summ.price.output / 1e6;
  const once = (keep + memo) * (pi - pc + pw) + archive * si + memo * so;
  const saving = archive * pc;
  const be = saving > 0 ? once / saving : Infinity;
  return { once, saving, be };
}

function renderSim(models) {
  if (!models.main.resolved || !models.summarizer.resolved) {
    $("sim").innerHTML = `<div class="empty">两个模型都解析到价目后才能模拟。</div>`;
    return;
  }
  const memo = models.memo_tokens;
  $("sim").innerHTML = `
    <div class="sim">
      <div>
        <div class="slider-row">
          <div class="head"><span class="k">压缩线比例</span><span class="v" id="sim-r-v"></span></div>
          <input type="range" id="sim-r" min="0.3" max="0.8" step="0.01" />
        </div>
        <div class="slider-row">
          <div class="head"><span class="k">预期再聊轮数</span><span class="v" id="sim-n-v"></span></div>
          <input type="range" id="sim-n" min="1" max="20" step="1" />
        </div>
        <button class="btn primary" id="sim-apply">应用到配置</button>
      </div>
      <div>
        <div class="mini-ruler" id="sim-ruler"></div>
        <div class="sim-out" id="sim-out"></div>
      </div>
    </div>`;

  const rEl = $("sim-r");
  const nEl = $("sim-n");
  rEl.value = state.status.thresholds.ratio ?? 0.72;
  nEl.value = state.status.config.target_turns;

  const win = state.status.thresholds.window;
  const keep = state.status.thresholds.keep_recent;
  const builtin = state.sessions.builtin_fallback || win;

  const update = () => {
    const ratio = +rEl.value;
    const n = +nEl.value;
    const line = Math.round(win * ratio);
    const archive = Math.max(line - keep, 0);
    $("sim-r-v").textContent = `${(ratio * 100).toFixed(0)}%`;
    $("sim-n-v").textContent = n;
    const r = computeEval(models.main, models.summarizer, keep, archive, memo);

    const W = 1000;
    const x = (v) => logScale(win)(v) * W;
    $("sim-ruler").innerHTML = `<svg viewBox="0 0 ${W} 30" style="height:30px">
      <rect x="0" y="12" width="${W}" height="6" fill="var(--ink)" opacity="0.06"/>
      <rect x="${x(keep)}" y="12" width="${Math.max(0, x(line) - x(keep))}" height="6" fill="var(--accent)" opacity="0.5"/>
      <line x1="${x(keep)}" y1="6" x2="${x(keep)}" y2="24" stroke="var(--ink-3)" stroke-width="1.5"/>
      <line x1="${x(line)}" y1="6" x2="${x(line)}" y2="24" stroke="var(--accent)" stroke-width="1.5"/>
      <line x1="${x(builtin)}" y1="6" x2="${x(builtin)}" y2="24" stroke="var(--warn)" stroke-width="1.5"/>
    </svg>`;

    const net = n * r.saving - r.once;
    $("sim-out").innerHTML = `
      <div class="row"><span class="k">压缩线</span><span class="v">${fmt(line)}</span></div>
      <div class="row"><span class="k">归档段</span><span class="v">${fmt(archive)}</span></div>
      <div class="row"><span class="k">压一次成本</span><span class="v">${money(r.once)}</span></div>
      <div class="row"><span class="k">每轮少花</span><span class="v">${money(r.saving)}</span></div>
      <div class="row"><span class="k">打平点</span><span class="v">${Number.isFinite(r.be) ? r.be.toFixed(1) + " 轮" : "—"}</span></div>
      <div class="row" style="border:none"><span class="k">净省（${n} 轮）</span>
        <span class="v" style="color:${net > 0 ? "var(--ok)" : "var(--danger)"}">${money(net)}</span></div>`;
  };

  rEl.addEventListener("input", update);
  nEl.addEventListener("input", update);
  update();

  $("sim-apply").addEventListener("click", async () => {
    const btn = $("sim-apply");
    btn.disabled = true;
    btn.textContent = "保存中…";
    await bridge.apiPost("config", {
      threshold_ratio: +rEl.value,
      target_turns: +nEl.value,
    });
    btn.textContent = "已应用";
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "应用到配置";
      load();
    }, 700);
  });
}

/* ---------------- 台账 ---------------- */

function renderLedger(l) {
  if (!l.total) {
    $("ledger").innerHTML = `<div class="empty">台账是空的，插件启用后每次工具调用记一条。</div>`;
    return;
  }
  const entries = Object.entries(l.counts).filter(([, v]) => v > 0);
  const dist = entries
    .map(
      ([k, v]) =>
        `<span style="flex-grow:${v};background:${OUTCOME_COLOR[k]}" title="${OUTCOME_TEXT[k]} ${v}"></span>`,
    )
    .join("");
  const legend = entries
    .map(
      ([k, v]) =>
        `<div class="legend-item"><span class="swatch" style="background:${OUTCOME_COLOR[k]}"></span><b>${OUTCOME_TEXT[k]}</b> ${v}</div>`,
    )
    .join("");

  const a = l.archive_tokens;
  let hist = "";
  if (a.length) {
    const max = Math.max(...a);
    const buckets = new Array(18).fill(0);
    a.forEach((v) => buckets[Math.min(17, Math.floor((v / (max || 1)) * 17))]++);
    const top = Math.max(...buckets, 1);
    hist = `<div class="block-label">归档段大小分布，最大 ${fmtK(max)}</div>
      <div class="hist">${buckets.map((b) => `<div class="bar" style="height:${(b / top) * 100}%"></div>`).join("")}</div>`;
  }

  const compacted = l.compacted.length
    ? `<div class="block-label">最近压缩</div>
       <table><thead><tr>
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

  $("ledger").innerHTML = `<div class="dist">${dist}</div>
    <div class="legend" style="margin-top:12px">${legend}</div>
    ${hist}${compacted}`;
}

/* ---------------- 配置 ---------------- */

const FIELDS = [
  ["enable_compact", "bool", "启用 SoL-Astr", "关掉后模型看不到压缩工具，插件不介入。"],
  ["dry_run", "bool", "影子模式", "只记账，不调摘要、不改历史。先看触发率再关。"],
  ["keep_recent_tokens", "int", "保留段 tokens", "0 = 自动：窗口的 15%，上限 40000。"],
  ["min_archive_tokens", "int", "最小归档段 tokens", "0 = 自动：压缩线（窗口×比例）减去保留段。手填后压缩线 = 保留段 + 这个数。"],
  ["threshold_ratio", "float", "压缩线比例", "压缩线 = 窗口 × 这个比例，默认 0.72。给内置 82% 兜底留反应区。"],
  ["target_turns", "int", "预期再聊轮数", "估计话题结束后还会再聊几轮。净省 = 轮数 × 每轮少花 − 压一次成本，为负就是赔钱。"],
  ["strip_tool_trace", "bool", "剥离工具痕迹", "压缩成功那轮，落盘前删掉工具调用记录，免得模型照着复读。"],
];

function renderConfig(status) {
  const c = status.config;
  const core = status.core;
  $("config").innerHTML =
    FIELDS.map(
      ([key, type, label, hint]) => `
      <div class="form-row">
        <div class="desc"><div class="k">${label}</div><div class="h">${hint}</div></div>
        ${
          type === "bool"
            ? `<label class="switch"><input type="checkbox" data-key="${key}" ${c[key] ? "checked" : ""}/><span class="track"></span></label>`
            : `<input type="number" data-key="${key}" data-type="${type}" step="${type === "float" ? "0.01" : "1"}" value="${c[key]}" />`
        }
      </div>`,
    ).join("") +
    `<div style="margin-top:18px"><button class="btn primary" id="save-config">保存</button></div>
     <div class="block-label">核心配置，只读，去「上下文管理策略」里改</div>
     <div class="readonly-grid">
       ${[
         ["压缩模型", core.provider_id || "未配置，回落当前聊天模型"],
         ["提示词来源", core.instruction_source],
         ["按轮截断", core.max_turns],
         ["溢出策略", core.overflow_strategy || "—"],
         ["内置保留比例", core.keep_recent_ratio],
         ["窗口兜底值", fmt(core.fallback_max_tokens)],
       ]
         .map(([k, v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`)
         .join("")}
     </div>
     <details><summary>发给摘要模型的完整 prompt</summary><pre>${esc(status.prompt_preview)}</pre></details>`;

  $("save-config").addEventListener("click", async () => {
    const btn = $("save-config");
    const body = {};
    $("config")
      .querySelectorAll("[data-key]")
      .forEach((el) => {
        body[el.dataset.key] =
          el.type === "checkbox" ? el.checked : el.dataset.type === "float" ? parseFloat(el.value) : +el.value;
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
  renderStatus(status, ledger);
  renderRuler(sessions, status.thresholds.window_source);
  renderSessions(sessions);
  renderModels(models, status);
  renderSim(models);
  renderLedger(ledger);
  renderConfig(status);
}

await bridge.ready();
$("refresh").addEventListener("click", load);
await load();
