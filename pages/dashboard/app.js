/* SoL-Astr 面板。请求全部走 bridge —— 页面在 iframe 里，自己 fetch 拿不到 JWT。 */

const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

const MODE_TEXT = { off: "未启用", dry_run: "影子模式", live: "生效中" };
const OUTCOME_TEXT = {
  compacted: "已压缩",
  dry_run: "影子记录",
  empty_summary: "摘要为空",
  non_positive_saving: "没啥可压",
  horizon_unavailable: "估不出轮数",
  price_unavailable: "没价目",
  deferred_economic: "不划算",
  deferred_subsequent_margin: "间隔太短",
  deferred_carried_debt: "旧债没还完",
};
const OUTCOME_COLOR = {
  compacted: "var(--accent)",
  dry_run: "var(--warn)",
  empty_summary: "var(--danger)",
  non_positive_saving: "var(--ink-3)",
  horizon_unavailable: "var(--ink-3)",
  price_unavailable: "var(--warn)",
  deferred_economic: "#a1a1aa",
  deferred_subsequent_margin: "#a1a1aa",
  deferred_carried_debt: "#a1a1aa",
};
const REASON_TEXT = {
  economic: "划算",
  window_protection: "越过窗口保护线，无条件压",
  non_positive_saving: "归档段还没 memo 长，压不出节省",
  horizon_unavailable: "还没有增速数据，估不出还能聊几轮",
  price_unavailable: "拿不到价目，经济账算不了",
  deferred_economic: "打平轮数 > 估计还能聊的轮数",
  deferred_subsequent_margin: "距上次压缩太短，打平轮数 ×1.5 还不够",
  deferred_carried_debt: "上一次压缩的成本还没省回来",
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
  const t = status.limits;
  const tag = $("mode-tag");
  tag.dataset.mode = status.mode;
  tag.textContent = MODE_TEXT[status.mode] || status.mode;

  $("readout").textContent =
    status.mode === "off"
      ? "插件没开。到下面配置里打开总开关，模型才看得见压缩工具。"
      : status.mode === "dry_run"
        ? "影子模式：账照算，不动历史，只记账。"
        : "生效中。话题翻篇时模型叫插件算账：划算就压；越过窗口保护线无条件压。";

  const rate = ledger.total ? `${(ledger.compact_rate * 100).toFixed(0)}%` : "—";
  const core = status.core;
  const coreGoverns =
    core.overflow_strategy === "llm_compress" && t.core_fallback < t.window_protection;
  const protNote = coreGoverns
    ? `核心 ${fmtK(t.core_fallback)} 先动手，它管不上`
    : `窗口 − ${fmt(status.constants.window_reserve)}，过线必压`;
  const cells = [
    ["窗口保护线", fmtK(t.window_protection), "tokens", protNote],
    ["窗口", fmtK(t.window), "tokens", `来源 ${esc(t.window_source)}`],
    ["压缩率", rate, "", ledger.total ? `${ledger.total} 次判账` : "还没数据"],
    [
      "已压缩",
      String(ledger.counts.compacted || 0),
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
  const { limits, sessions: rows } = sessions;
  const win = limits.window;
  const keep = limits.keep_recent;
  const prot = limits.window_protection;
  const core = limits.core_fallback;
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
    <rect x="0" y="${y - 8}" width="${x(keep)}" height="16" fill="var(--ink)" opacity="0.08"/>
    <rect x="${x(keep)}" y="${y - 8}" width="${Math.max(0, x(prot) - x(keep))}" height="16" fill="var(--accent-soft)"/>
    <rect x="${x(prot)}" y="${y - 8}" width="${W - x(prot)}" height="16" fill="url(#hatch)"/>
    <rect x="${x(prot)}" y="${y - 8}" width="${W - x(prot)}" height="16" fill="var(--warn-bg)"/>
    <line x1="0" y1="${y + 8}" x2="${W}" y2="${y + 8}" stroke="var(--line)" stroke-width="1"/>`;

  const tick = (v, label, color, above, dash) => `
    <line x1="${x(v)}" y1="${above ? y - 22 : y + 8}" x2="${x(v)}" y2="${above ? y - 8 : y + 20}" stroke="${color}" stroke-width="1.5" ${dash ? 'stroke-dasharray="3 2"' : ""}/>
    <text x="${x(v)}" y="${above ? y - 30 : y + 34}" fill="${color}" font-size="11.5"
      font-family="var(--mono)" text-anchor="middle">${label}</text>`;

  const ticks =
    tick(prot, `保护线 ${fmtK(prot)}`, "var(--warn)", true) +
    tick(keep, `保留 ${fmtK(keep)}`, "var(--ink-3)", false) +
    tick(core, `核心 ${fmtK(core)}`, "var(--ink-3)", false, true);

  const rugs = rows
    .map((s) => {
      const over = s.tokens >= prot;
      return `<line x1="${x(s.tokens)}" y1="${y + 10}" x2="${x(s.tokens)}" y2="${y + 26}"
        stroke="${over ? "var(--warn)" : "var(--ink-3)"}" stroke-width="2"
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

  const over = rows.filter((s) => s.tokens >= prot).length;
  $("ruler-legend").innerHTML =
    [
      ["rgba(128,128,128,0.15)", "保留段", "压缩后原样留下的最近对话"],
      ["var(--accent-soft)", "判账区", "话题翻篇时算账，划算就压"],
      ["var(--warn-bg)", "保护区", `${over} 个会话在这里，无条件压`],
    ]
      .map(
        ([c, k, v]) =>
          `<div class="legend-item"><span class="swatch" style="background:${c}"></span><b>${k}</b> ${v}</div>`,
      )
      .join("") +
    `<div class="legend-item">虚线 = 核心 82% 兜底（它不翻篇也压），竖线 = 会话用量</div>`;
}

/* ---------------- 会话 ---------------- */

function renderSessions(sessions) {
  const { limits, sessions: rows } = sessions;
  const prot = limits.window_protection;
  $("sessions").innerHTML = rows.length
    ? `<table><thead><tr><th>会话</th><th class="right">用量</th><th class="right">每轮增量</th><th class="right">已压</th></tr></thead>
       <tbody>${rows
         .slice(0, 12)
         .map((s) => {
           const over = s.tokens >= prot;
           const named = s.title && s.title !== "未命名会话";
           const inc = s.state && s.state.average_increment;
           const comp = s.state ? s.state.compaction_count : 0;
           return `<tr><td>${esc(named ? s.title : s.user_id)}${
             named ? `<div class="umo">${esc(s.user_id)}</div>` : ""
           }</td>
             <td class="right num" style="color:${over ? "var(--warn)" : "inherit"}">${fmt(s.tokens)}</td>
             <td class="right num">${inc ? fmt(Math.round(inc)) : "—"}</td>
             <td class="right num">${comp || "—"}</td></tr>`;
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
  const d = status.live_decision;
  let verdict = `<div class="verdict">还没有会话数据，聊几轮后这里会现场判一次账。</div>`;
  if (d) {
    const head = `拿「${esc(d.sample_title)}」（${fmtK(d.sample_tokens)}）现场判账：`;
    verdict = d.compact
      ? `<div class="verdict">${head}<b>现在压</b>——${esc(REASON_TEXT[d.reason] || d.reason)}。
        压一次花 <span class="num">${money(d.one_time_cost)}</span>，之后每轮少花
        <span class="num">${money(d.per_request_saving)}</span>${
          d.breakeven_requests ? `，第 <b>${d.breakeven_requests.toFixed(1)}</b> 轮打平` : ""
        }。</div>`
      : `<div class="verdict" data-bad="1">${head}<b>先不压</b>——${esc(REASON_TEXT[d.reason] || d.reason)}${
          d.breakeven_requests && d.horizon_requests != null
            ? `（打平 ${d.breakeven_requests.toFixed(1)} 轮，估计还能聊 ${d.horizon_requests} 轮）`
            : ""
        }。</div>`;
  }

  $("models").innerHTML =
    `<div class="models">${modelBlock(models.main)}<div class="arrow">-&gt;</div>${modelBlock(models.summarizer)}</div>${verdict}
     <div class="footnote">目录共 ${models.catalog_size} 个模型，单位 $/1M tokens。走聚合渠道实付可能不同，这里只比谁划算。</div>`;
}

/* ---------------- 模拟 ---------------- */

/* 判账的前端复刻，公式与 sol_astr/economics.py 保持一致。 */

function computeEval(main, summ, keep, archive, memo) {
  const pi = main.price.input / 1e6;
  const pc = main.price.cache_read_effective / 1e6;
  const pw = main.price.cache_write / 1e6;
  const si = summ.price.input / 1e6;
  const so = summ.price.output / 1e6;
  const once = (keep + memo) * (pi - pc + pw) + archive * si + memo * so;
  const saving = Math.max(0, archive - memo) * pc;
  const be = saving > 0 ? once / saving : Infinity;
  return { once, saving, be };
}

function renderSim(models) {
  if (!models.main.resolved || !models.summarizer.resolved) {
    $("sim").innerHTML = `<div class="empty">两个模型都解析到价目后才能模拟。</div>`;
    return;
  }
  const memo = models.memo_tokens;
  const win = state.status.limits.window;
  const prot = state.status.limits.window_protection;
  const keep0 = state.status.limits.keep_recent;
  const live = state.status.live_decision;
  const inc0 =
    (state.sessions.sessions.find((s) => s.state && s.state.average_increment) || {}).state
      ?.average_increment || 3000;
  const arch0 = live ? live.archive_estimate : 60000;

  $("sim").innerHTML = `
    <div class="sim">
      <div>
        <div class="slider-row">
          <div class="head"><span class="k">保留段</span><span class="v" id="sim-k-v"></span></div>
          <input type="range" id="sim-k" min="2000" max="100000" step="1000" />
        </div>
        <div class="slider-row">
          <div class="head"><span class="k">假设归档段</span><span class="v" id="sim-a-v"></span></div>
          <input type="range" id="sim-a" min="5000" max="500000" step="5000" />
        </div>
        <div class="slider-row">
          <div class="head"><span class="k">每轮增量</span><span class="v" id="sim-i-v"></span></div>
          <input type="range" id="sim-i" min="500" max="20000" step="500" />
        </div>
        <button class="btn primary" id="sim-apply">保留段写进配置</button>
      </div>
      <div>
        <div class="mini-ruler" id="sim-ruler"></div>
        <div class="sim-out" id="sim-out"></div>
      </div>
    </div>`;

  const kEl = $("sim-k");
  const aEl = $("sim-a");
  const iEl = $("sim-i");
  kEl.value = keep0;
  aEl.value = Math.min(500000, Math.max(5000, arch0));
  iEl.value = Math.min(20000, Math.max(500, Math.round(inc0 / 500) * 500));

  const update = () => {
    const keep = +kEl.value;
    const archive = +aEl.value;
    const inc = +iEl.value;
    $("sim-k-v").textContent = fmtK(keep);
    $("sim-a-v").textContent = fmtK(archive);
    $("sim-i-v").textContent = fmt(inc);

    const r = computeEval(models.main, models.summarizer, keep, archive, memo);
    const used = keep + archive;
    const horizon = Math.max(0, Math.floor((win - used) / inc));
    const go = r.be <= horizon && archive > memo;

    const W = 1000;
    const x = (v) => logScale(win)(v) * W;
    $("sim-ruler").innerHTML = `<svg viewBox="0 0 ${W} 30" style="height:30px">
      <rect x="0" y="12" width="${W}" height="6" fill="var(--ink)" opacity="0.06"/>
      <rect x="${x(keep)}" y="12" width="${Math.max(0, x(used) - x(keep))}" height="6" fill="var(--accent)" opacity="0.5"/>
      <line x1="${x(keep)}" y1="6" x2="${x(keep)}" y2="24" stroke="var(--ink-3)" stroke-width="1.5"/>
      <line x1="${x(used)}" y1="6" x2="${x(used)}" y2="24" stroke="var(--accent)" stroke-width="1.5"/>
      <line x1="${x(prot)}" y1="6" x2="${x(prot)}" y2="24" stroke="var(--warn)" stroke-width="1.5"/>
    </svg>`;

    $("sim-out").innerHTML = `
      <div class="row"><span class="k">压一次成本</span><span class="v">${money(r.once)}</span></div>
      <div class="row"><span class="k">每轮少花</span><span class="v">${money(r.saving)}</span></div>
      <div class="row"><span class="k">打平轮数</span><span class="v">${Number.isFinite(r.be) ? r.be.toFixed(1) : "—"}</span></div>
      <div class="row"><span class="k">窗口还装得下</span><span class="v">${horizon} 轮</span></div>
      <div class="row" style="border:none"><span class="k">首次压缩判决</span>
        <span class="v" style="color:${go ? "var(--ok)" : "var(--danger)"}">${go ? "压" : "不压"}</span></div>
      <div class="footnote">后续压缩更严：打平轮数 ×1.5 还要 ≤ 装得下的轮数，且上一次的债得先还清。</div>`;
  };

  [kEl, aEl, iEl].forEach((el) => el.addEventListener("input", update));
  update();

  $("sim-apply").addEventListener("click", async () => {
    const btn = $("sim-apply");
    btn.disabled = true;
    btn.textContent = "保存中…";
    await bridge.apiPost("config", {
      keep_recent_tokens: +kEl.value,
    });
    btn.textContent = "已应用";
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "保留段写进配置";
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
  ["dry_run", "bool", "影子模式", "账照算，不调摘要、不改历史。先看判账记录再关。"],
  ["keep_recent_tokens", "int", "保留段 tokens", "0 = 默认 20000（SoL-Pi 源码 DEFAULT_KEEP_RECENT_TOKENS）。压缩后原样留下的最近对话。"],
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
  renderRuler(sessions, status.limits.window_source);
  renderSessions(sessions);
  renderModels(models, status);
  renderSim(models);
  renderLedger(ledger);
  renderConfig(status);
}

await bridge.ready();
$("refresh").addEventListener("click", load);
await load();
