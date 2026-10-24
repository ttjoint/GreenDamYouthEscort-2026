/**
 * The parent console.
 *
 * One rule shapes this file: **never build HTML from data**. Everything is
 * created with `document.createElement` and filled with `textContent`. The data
 * comes from the local service, which today produces only counts and tokens, but
 * a log record's `reason` or a browser's name is still data - and a console that
 * uses `innerHTML` is one careless field away from executing whatever is in it.
 * The CSP already forbids inline script; this makes the mistake impossible
 * rather than merely blocked.
 *
 * The second rule is that "not assessed" must never look like "fine". The status
 * view leads with what the filter is *not* checking, and a filter with no word
 * lists gets a grey dot and the word 未判定, not a green tick.
 */
"use strict";

const TOKEN = "__CONSOLE_TOKEN__";

/** Human labels for the gap tokens the data layer emits. */
const GAP_TEXT = {
  filtering_disabled: ["过滤已关闭", "策略层把过滤整体关掉了，所有判定都不会产生动作。"],
  keyword_filter_disabled: ["关键词过滤已关闭", "文本层不工作，只有网络层仍在匹配。"],
  no_word_lists: ["没有词库", "没有任何词库启用，因此不会检查网页文字。这不是「内容干净」。"],
  no_image_backend: ["没有图像模型", "未安装图像模型，因此不会检查图片。这同样不是「图片安全」。"],
  no_network_rules: ["没有网络规则", "名单 A–D 全为空，域名与进程层没有可匹配的条目。"],
};

const SWITCH_TEXT = {
  filtering_disabled: "过滤总开关（关闭 = 不拦截）",
  keyword_filter_disabled: "关键词过滤开关",
  image_filter_enabled: "图像过滤开关",
};

const COVERAGE_TEXT = {
  text: "网页文字",
  image: "图片",
  network: "域名 / 进程",
};

/** The last payload, so switching tabs does not refetch. */
let STATE = null;

// --- helpers ----------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function pill(text, kind) {
  return el("span", `pill ${kind}`, text);
}

/** A two-column table row. */
function row(left, right) {
  const tr = el("tr");
  const th = el("th", null, left);
  const td = el("td");
  if (right instanceof Node) td.appendChild(right);
  else td.textContent = right === undefined || right === null ? "—" : String(right);
  tr.appendChild(th);
  tr.appendChild(td);
  return tr;
}

/** A table with a header row and body rows. */
function table(node, headers, rows) {
  clear(node);
  const thead = el("thead");
  const htr = el("tr");
  for (const name of headers) htr.appendChild(el("th", null, name));
  thead.appendChild(htr);
  node.appendChild(thead);

  const tbody = el("tbody");
  for (const cells of rows) {
    const tr = el("tr");
    for (const cell of cells) {
      const td = el("td");
      if (cell instanceof Node) td.appendChild(cell);
      else td.textContent = cell === undefined || cell === null ? "" : String(cell);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  if (rows.length === 0) {
    const tr = el("tr");
    const td = el("td", "empty", "没有内容。");
    td.colSpan = headers.length;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  node.appendChild(tbody);
}

async function fetchOverview() {
  const response = await fetch(`/api/overview?token=${encodeURIComponent(TOKEN)}`, {
    headers: { "X-Console-Token": TOKEN },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// --- views ------------------------------------------------------------

function renderStatus(status) {
  const verdict = document.getElementById("verdict");
  clear(verdict);

  let kind = "off";
  let headline = "未在检查任何内容";
  let sub = "没有词库也没有图像模型，过滤器看不到任何内容。";

  if (status.filtering_disabled) {
    kind = "off";
    headline = "过滤已关闭";
    sub = "策略层关闭了过滤，所有判定都不会产生动作。";
  } else if (status.assessing) {
    const parts = [];
    if (status.coverage.text) parts.push(`文字（${status.packs.terms} 条词条）`);
    if (status.coverage.image) parts.push("图片");
    if (status.coverage.network) parts.push("域名 / 进程");
    kind = status.gaps.length ? "partial" : "ok";
    headline = status.gaps.length ? "部分检查中" : "正在检查";
    sub = `已覆盖：${parts.join("、")}。`;
  }

  verdict.className = `verdict ${kind}`;
  verdict.appendChild(el("span", "dot"));
  const text = el("div");
  text.appendChild(el("strong", null, headline));
  text.appendChild(el("span", "sub", sub));
  verdict.appendChild(text);

  // Coverage table
  const coverage = document.getElementById("coverage");
  clear(coverage);
  for (const [key, label] of Object.entries(COVERAGE_TEXT)) {
    const on = Boolean(status.coverage[key]);
    coverage.appendChild(row(label, on ? pill("已覆盖", "on") : pill("未覆盖", "gap")));
  }
  coverage.appendChild(row("生效词条", `${status.packs.terms} 条 / ${status.packs.usable} 个词库`));
  coverage.appendChild(row("记录条数", status.log_records));

  // Gaps
  const gaps = document.getElementById("gaps");
  clear(gaps);
  if (status.gaps.length === 0) {
    gaps.appendChild(el("li", null, "没有缺口。"));
  } else {
    for (const token of status.gaps) {
      const entry = GAP_TEXT[token] || [token, ""];
      const li = el("li");
      li.appendChild(el("strong", null, entry[0]));
      if (entry[1]) li.appendChild(el("div", "why", entry[1]));
      gaps.appendChild(li);
    }
  }

  // Switches
  const switches = document.getElementById("switches");
  clear(switches);
  for (const [key, label] of Object.entries(SWITCH_TEXT)) {
    const value = status[key];
    let shown;
    if (key === "image_filter_enabled") {
      shown = value ? pill("开", "on") : pill("关", "off");
    } else {
      // For the two "disabled" switches, true is bad.
      shown = value ? pill("已关闭", "gap") : pill("正常", "on");
    }
    switches.appendChild(row(label, shown));
  }
}

function renderLog(log) {
  const meta = document.getElementById("log-meta");
  if (!log.path_known) {
    meta.textContent = "还没有日志文件。过滤器的第一次判定会创建它。";
  } else if (log.total === 0) {
    meta.textContent = "日志里还没有记录。";
  } else if (log.truncated) {
    meta.textContent = `共 ${log.total} 条，显示最近 ${log.rows.length} 条。`;
  } else {
    meta.textContent = `共 ${log.total} 条。`;
  }

  const rows = log.rows.map((entry) => {
    const event = entry.event || "";
    const kind = event === "block" ? "blocked"
               : event === "allow" ? "on"
               : event === "gated" ? "gap" : "off";
    const when = (entry.when || "").replace("T", " ").replace("Z", "");
    return [
      entry.seq === undefined ? "" : String(entry.seq),
      when,
      pill(event || "—", kind),
      entry.source || "",
      entry.stage || "",
      entry.reason || "",
      entry.content_digest ? el("span", "mono", entry.content_digest) : "—",
    ];
  });

  table(document.getElementById("log-table"),
        ["#", "时间 (UTC)", "事件", "来源", "阶段", "原因", "内容摘要"],
        rows);
}

function renderPacks(packs) {
  const summary = document.getElementById("packs-summary");
  if (!packs) {
    summary.textContent = "未提供词库信息。";
    return;
  }
  summary.textContent = packs.total_terms === 0
    ? "没有任何词库启用。文本过滤不会隐藏任何内容。"
    : `生效 ${packs.usable} 个词库，合计 ${packs.total_terms} 条词条。`;

  const rows = packs.packs.map((pack) => [
    pack.id,
    pack.name,
    pack.category || "—",
    String(pack.terms),
    pack.enabled ? pill("启用", "on") : pill("停用", "off"),
    pack.usable ? "" : (pack.error || "不可用"),
    pack.licence || "—",
  ]);

  table(document.getElementById("packs-table"),
        ["id", "名称", "类别", "词条数", "状态", "问题", "许可"],
        rows);
}

function renderBrowsers(data) {
  const rows = (data.browsers || []).map((browser) => [
    browser.name,
    browser.family,
    browser.version || "—",
    browser.configurable ? pill("可配置", "on") : pill("不支持", "gap"),
  ]);

  table(document.getElementById("browsers-table"),
        ["浏览器", "族", "版本", "过滤器"], rows);

  const warn = document.getElementById("browsers-uncovered");
  if (data.uncovered && data.uncovered.length) {
    warn.hidden = false;
    clear(warn);
    warn.appendChild(el("strong", null, "以下浏览器不受本产品覆盖："));
    warn.appendChild(el("div", null, data.uncovered.join("、")));
    warn.appendChild(el("div", null,
      "在这些浏览器里过滤器不生效。这不是「它们上面内容是安全的」。"));
  } else {
    warn.hidden = true;
  }
}

function renderImage(image) {
  const verdict = document.getElementById("image-verdict");
  clear(verdict);

  verdict.className = `verdict ${image.assessing ? "ok" : "off"}`;
  verdict.appendChild(el("span", "dot"));
  const text = el("div");
  text.appendChild(el("strong", null,
    image.assessing ? "图像判定已启用" : "未安装图像模型"));
  text.appendChild(el("span", "sub",
    image.assessing ? `后端：${image.backend}` : "不会检查任何图片"));
  verdict.appendChild(text);

  document.getElementById("image-explanation").textContent = image.explanation;

  const table_ = document.getElementById("image-table");
  clear(table_);
  table_.appendChild(row("后端", image.backend || "无"));
  table_.appendChild(row("判定阈值", image.assessing ? String(image.threshold) : "—"));
  table_.appendChild(row("状态", image.assessing ? pill("已启用", "on") : pill("未启用", "gap")));
}

// --- wiring -----------------------------------------------------------

function show(view) {
  for (const button of document.querySelectorAll(".tab")) {
    button.setAttribute("aria-selected", String(button.dataset.view === view));
  }
  for (const section of document.querySelectorAll(".view")) {
    section.hidden = section.id !== `view-${view}`;
  }
}

function renderAll(payload) {
  STATE = payload;
  document.getElementById("stamp").textContent = (payload.when || "").replace("T", " ").replace("Z", "");
  renderStatus(payload.status);
  renderLog(payload.log);
  renderImage(payload.image);
  if (payload.packs) renderPacks(payload.packs);
  if (payload.browsers) renderBrowsers(payload.browsers);
}

async function refresh() {
  try {
    renderAll(await fetchOverview());
  } catch (error) {
    const verdict = document.getElementById("verdict");
    clear(verdict);
    verdict.className = "verdict off";
    verdict.appendChild(el("span", "dot"));
    const text = el("div");
    text.appendChild(el("strong", null, "无法读取状态"));
    text.appendChild(el("span", "sub",
      "本地服务没有响应。控制台可能已经关闭。"));
    verdict.appendChild(text);
    console.debug("[console] refresh failed", error);
  }
}

function main() {
  for (const button of document.querySelectorAll(".tab")) {
    button.addEventListener("click", () => show(button.dataset.view));
  }
  show("status");
  refresh();
  // Refresh while visible, and stop when the tab is hidden - a console left open
  // in the background should not poll forever.
  setInterval(() => {
    if (!document.hidden) refresh();
  }, 5000);
}

main();
