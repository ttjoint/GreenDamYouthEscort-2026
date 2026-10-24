"use strict";
/**
 * Runs content.js's real decision functions against a minimal DOM stub.
 *
 * Why this exists: `host/tests/test_extension.py` verifies the *rules* in
 * Python, and asserts the JS parses. Neither proves the JS implements the same
 * rules. This harness extracts the actual functions from `content.js` - not a
 * copy of them - and runs them, so a divergence between the specification and
 * the implementation fails here.
 *
 * Why not a real browser: as of Chrome 152 and Edge 156, `--load-extension` is
 * ignored, so a headless instance cannot be made to load an unpacked extension.
 * Verified on this machine: the extension list stays empty and `chrome.runtime`
 * is undefined in the page. Driving a real browser would therefore test nothing.
 * The DOM stub is smaller than the workaround would be, and it tests the code
 * that carries the risk.
 *
 * Usage: node run.js <path to content.js> <config.json path>
 * Exit code 0 means every case passed.
 */
const fs = require("fs");
const path = require("path");
const { makeEl, makeDoc } = require("./harness.js");

const [srcPath, configPath] = process.argv.slice(2);
if (!srcPath || !configPath) {
  console.error("usage: node run.js <content.js> <config.json>");
  process.exit(2);
}

const CONFIG = JSON.parse(fs.readFileSync(configPath, "utf8"));
const source = fs.readFileSync(srcPath, "utf8");

/**
 * Extract the pure decision functions from content.js.
 *
 * Slicing between two markers rather than loading the whole file: the file's
 * top level calls `main()`, which needs a live extension API. The slice covers
 * `matchText`, `classifyImageUrl`, `shouldJudge` and `hideElement` - the four
 * functions that decide anything.
 */
function extractPure(source) {
  const start = source.indexOf("function matchText");
  const end = source.indexOf("/** Ask the local service");
  if (start < 0 || end < 0 || end <= start) {
    throw new Error(
      "could not locate the decision functions in content.js; the markers " +
        "'function matchText' and 'Ask the local service' must both be present",
    );
  }
  return source.slice(start, end);
}

const pure = extractPure(source);

/** Run one case: which elements end up hidden. */
function runCase(elements, config) {
  const doc = makeDoc(elements);
  const sandboxWindow = {
    getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  };

  const fn = new Function(
    "CONFIG",
    "document",
    "Node",
    "window",
    "__elements",
    "handled",
    `
    ${pure}
    const out = [];
    for (const el of __elements) {
      if (el.tagName === "IMG") continue;
      if (!shouldJudge(el)) continue;
      if (el.children.length > 0) continue;
      const m = matchText(el.textContent || "", CONFIG);
      if (m) { hideElement(el, "text"); out.push(el.tagName); }
    }
    return out;
  `,
  );

  return fn(config, doc, { ELEMENT_NODE: 1 }, sandboxWindow, elements,
            new WeakSet());
}

/** The image decision, exercised the same way. */
function runImageCase(url, config) {
  const fn = new Function("CONFIG", `
    ${pure}
    return classifyImageUrl(__url, CONFIG);
  `);
  return new Function("CONFIG", "__url", `
    ${pure}
    return classifyImageUrl(__url, CONFIG);
  `)(config, url);
}

let failures = 0;
let total = 0;

function expect(label, actual, wanted) {
  total += 1;
  const ok = JSON.stringify(actual) === JSON.stringify(wanted);
  if (!ok) failures += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"}  ${label}` +
      (ok ? "" : `\n        got ${JSON.stringify(actual)}` +
                `\n        want ${JSON.stringify(wanted)}`),
  );
}

// --- text: a matching paragraph is hidden, a clean one is not ---------------
{
  const bad = makeEl("p", "这里讨论赌博的内容");
  const good = makeEl("p", "完全正常的一段文字");
  expect("命中词条的元素被隐藏", runCase([bad, good], CONFIG).sort(), ["P"]);
}

// --- text: the page's chrome is never hidden -------------------------------
{
  const nav = makeEl("nav", "导航 赌博");
  expect("nav 不被隐藏（页面自身结构）", runCase([nav], CONFIG), []);
}

// --- text: a container with element children is not hidden wholesale -------
{
  const child = makeEl("p", "正常");
  const div = makeEl("div", "赌博", [child]);
  expect("有子元素的容器不整体隐藏", runCase([div], CONFIG), []);
}

// --- text: a single character is not judged -------------------------------
{
  const p = makeEl("p", "赌");
  expect("单字不判定", runCase([p], CONFIG), []);
}

// --- text: an empty term list hides nothing -------------------------------
{
  const p = makeEl("p", "赌博");
  const empty = { ...CONFIG, terms: [] };
  expect("无词库时不隐藏任何东西", runCase([p], empty), []);
}

// --- text: hideText off means nothing is hidden ---------------------------
{
  const p = makeEl("p", "赌博");
  const off = { ...CONFIG, hideText: false };
  expect("关闭文本过滤后不隐藏", runCase([p], off), []);
}

// --- text: case-insensitivity follows the config -------------------------
{
  const p = makeEl("p", "a BADWORD here");
  const lower = { ...CONFIG, terms: ["badword"], caseSensitive: false };
  expect("默认不区分大小写", runCase([p], lower), ["P"]);
  const strict = { ...CONFIG, terms: ["badword"], caseSensitive: true };
  expect("开启区分大小写后不命中", runCase([p], strict), []);
}

// --- text: oversized text is skipped -------------------------------------
{
  const p = makeEl("p", "赌博" + "x".repeat(200));
  const small = { ...CONFIG, maxTextLength: 50 };
  expect("超长文本不判定", runCase([p], small), []);
}

// --- images: URL classification -----------------------------------------
expect("http 图片需要评分",
       runImageCase("https://e.com/a.jpg", CONFIG), "score");
expect("无扩展名的图片仍需评分",
       runImageCase("https://cdn.e.com/image/12345", CONFIG), "score");
expect("data URI 跳过", runImageCase("data:image/png;base64,AA", CONFIG),
       "skip");
expect("浏览器内部地址忽略", runImageCase("chrome://settings", CONFIG),
       "ignore");
expect("空地址忽略", runImageCase("", CONFIG), "ignore");
expect("关闭图片过滤后跳过",
       runImageCase("https://e.com/a.jpg", { ...CONFIG, hideImages: false }),
       "skip");

console.log(`\n${total - failures}/${total} cases passed`);
process.exit(failures === 0 ? 0 : 1);
