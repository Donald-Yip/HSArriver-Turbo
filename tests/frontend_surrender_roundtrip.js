/* 前端往返校验：把 index.html 的脚本放进假 DOM 里跑，检查
   「嵌套配置 → 表单 state → 收集回配置」能不能原样往返，以及折叠状态是否记住。
   由 tests/test_surrender_frontend.py 通过 node 调起（没有 node 就跳过）。 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "web", "index.html"), "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("no script"); process.exit(1); }

function fakeEl() {
  const el = {
    innerHTML: "", value: "", checked: false, textContent: "", dataset: {},
    style: {}, isConnected: true, parentNode: null,
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return fakeEl(); },
    querySelectorAll() { return []; },
    closest() { return null; },
    hasAttribute() { return false; },
    getAttribute() { return null; },
    setAttribute() {}, appendChild() {}, scrollIntoView() {},
  };
  return el;
}

const store = {};
const els = {};
const noop = () => {};
const chain = { then() { return chain; }, catch() { return chain; } };

const ctx = {
  console,
  document: {
    querySelectorAll() { return []; },
    addEventListener() {},
    getElementById(id) { return els[id] = els[id] || fakeEl(); },
    createElement() { return fakeEl(); },
  },
  window: {},
  localStorage: {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
    removeItem(k) { delete store[k]; },
  },
  $: (id) => (els[id] = els[id] || fakeEl()),
  get: () => chain,
  post: () => chain,
  put: () => chain,
  del: () => chain,
  toast: noop,
  fetch: () => chain,
  confirm: () => true,
  alert: noop,
  setInterval: () => 0,
  clearInterval: noop,
  setTimeout: () => 0,
  clearTimeout: noop,
  requestAnimationFrame: noop,
  location: { reload: noop },
};
ctx.globalThis = ctx;

vm.createContext(ctx);
try {
  vm.runInContext(m[1], ctx, { filename: "index.html<script>" });
} catch (err) {
  console.error("LOAD FAILED:", err && err.message);
  process.exit(2);
}

const cases = [
  {
    name: "A 且 (B 或 C)",
    cfg: {
      enabled: true,
      decks: [{
        id: "nested", name: "嵌套", class: "PRIEST",
        when: { all: [
          { type: "played", side: "opponent", card: "神秘访客" },
          { any: [
            { type: "played", side: "opponent", card: "火球术" },
            { type: "played", side: "opponent", card: "寒冰箭", within: { max: 4 } },
          ] },
        ] },
      }],
    },
  },
  {
    name: "非 (B 或 C)",
    cfg: {
      enabled: true,
      decks: [{ id: "neg", name: "非", class: "",
                when: { not: { any: [
                  { type: "played", side: "opponent", card: "火球术" },
                  { type: "played", side: "opponent", card: "寒冰箭" },
                ] } } }],
    },
  },
  {
    name: "旧单层配置 + enabled:false + deck id",
    cfg: {
      enabled: true,
      decks: [{ id: "old", name: "老配置", enabled: false, class: "PRIEST",
                when: { all: [
                  { type: "seen", side: "opponent", card: "裂魂者阿扎莉娜" },
                  { type: "played", side: "opponent", card: "神秘访客",
                    within: { max: 4 } },
                ] } }],
    },
  },
  {
    name: "表单表达不了的 turn（要原样保留）",
    cfg: {
      enabled: true,
      decks: [{ id: "turn", name: "回合", class: "",
                when: { all: [
                  { type: "turn", field: "round", max: 4 },
                  { type: "played", side: "opponent", card: "神秘访客" },
                ] } }],
    },
  },
  {
    name: "多卡数组 + card_type 原样保留",
    cfg: {
      enabled: true,
      decks: [{ id: "multi", name: "多卡", class: "",
                when: { all: [
                  { type: "played", side: "opponent",
                    card: ["神秘访客", "火球术"], at_least: 2 },
                  { type: "played", side: "opponent", card: "寒冰箭",
                    card_type: "SPELL" },
                ] } }],
    },
  },
];

function strip(value) {
  return JSON.parse(JSON.stringify(value).replace(/"within":\{"field":"round",?/g,
                                                   '"within":{'));
}

let failed = 0;
for (const item of cases) {
  ctx.surrenderLoadConfig(JSON.parse(JSON.stringify(item.cfg)));
  const out = ctx.surrenderCollectConfig();
  const round = JSON.parse(JSON.stringify(out));
  const a = JSON.stringify(strip(item.cfg.decks[0].when));
  const b = JSON.stringify(strip(round.decks[0].when));
  const ok = a === b;
  if (!ok) {
    failed++;
    console.log("MISMATCH:", item.name);
    console.log("  in :", a);
    console.log("  out:", b);
  } else {
    console.log("OK:", item.name);
  }
  /* 卡组级字段也不能被表单改写（以前 enabled 会被硬编码成 true） */
  const deckIn = item.cfg.decks[0];
  const deckOut = round.decks[0];
  const deckOk = (deckIn.enabled !== false) === deckOut.enabled
    && (deckIn.action || "concede") === deckOut.action
    && (deckIn.name || "") === deckOut.name;
  if (!deckOk) {
    failed++;
    console.log("DECK FIELD LOST:", item.name, JSON.stringify(deckOut));
  }
}

/* 收起状态：记忆 + 不丢条件 */
ctx.surrenderLoadConfig({ enabled: true, decks: [{
  id: "fold", name: "折叠", class: "", when: { all: [
    { type: "played", side: "opponent", card: "神秘访客" }] } }] });
ctx.surrenderToggleDeck(ctx.surrenderDecks[0]);
const remembered = JSON.parse(ctx.localStorage.getItem("hsSurrenderCollapsed"));
console.log(remembered.fold === 1 ? "OK: 折叠已记住" : "FAIL: 折叠没记住");
ctx.surrenderLoadConfig({ enabled: true, decks: [{
  id: "fold", name: "折叠", class: "", when: { all: [
    { type: "played", side: "opponent", card: "神秘访客" }] } }] });
console.log(ctx.surrenderDecks[0].collapsed === true
  ? "OK: 重新载入仍是收起" : "FAIL: 重载丢失收起状态");
const folded = ctx.surrenderCollectConfig();
console.log(JSON.stringify(folded.decks[0].when)
  === JSON.stringify({ all: [{ type: "played", side: "opponent", card: "神秘访客" }] })
  ? "OK: 收起状态下保存不丢条件" : "FAIL: 收起后条件丢了");

/* 渲染出来的 DOM 片段要有嵌套组 + 收起 class + 摘要 */
ctx.surrenderLoadConfig(JSON.parse(JSON.stringify(cases[0].cfg)));
const rendered = els["surrenderDecks"].innerHTML;
const checks = [
  ['sgroup', rendered.includes('class="sgroup"')],
  ['data-path="1"', rendered.includes('data-path="1"')],
  ['op 下拉', rendered.includes('data-f="op"')],
  ['删除组按钮', rendered.includes('data-act="del-group"')],
  ['摘要', rendered.includes('sdeck-summary')],
];
ctx.surrenderToggleDeck(ctx.surrenderDecks[0]);
const foldedHtml = els["surrenderDecks"].innerHTML;
checks.push(['收起 class', foldedHtml.includes('sdeck collapsed')]);
checks.push(['收起箭头', foldedHtml.includes('▸')]);
for (const [label, ok] of checks) {
  console.log((ok ? "OK: " : "FAIL: ") + label);
  if (!ok) failed++;
}

process.exit(failed ? 1 : 0);