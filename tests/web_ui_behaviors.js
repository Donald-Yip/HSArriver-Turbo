/* Behavior checks for the real web/index.html script in a fake DOM. */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const group = process.argv[2] || "";
const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) {
  console.error("no script");
  process.exit(1);
}

function fakeEl(id) {
  const listeners = {};
  return {
    id: id || "",
    innerHTML: "",
    value: "",
    checked: false,
    disabled: false,
    textContent: "",
    title: "",
    className: "",
    min: "",
    dataset: {},
    style: {},
    childNodes: [],
    scrollHeight: 0,
    scrollTop: 0,
    clientHeight: 0,
    isConnected: true,
    parentNode: null,
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    dispatch(type, event) {
      for (const fn of listeners[type] || []) fn.call(this, event || { key: "" });
    },
    querySelector() { return fakeEl(); },
    querySelectorAll() { return []; },
    closest() { return null; },
    hasAttribute() { return false; },
    getAttribute() { return null; },
    setAttribute() {},
    appendChild(child) { this.childNodes.push(child); },
    removeChild(child) { this.childNodes = this.childNodes.filter((item) => item !== child); },
    scrollIntoView() {},
    blur() {},
  };
}

const els = {};
const store = {};
const requests = [];
const noop = () => {};
function getEl(id) { return (els[id] = els[id] || fakeEl(id)); }
function chain(value) {
  const next = {
    then(fn) {
      const result = fn(value);
      return result && typeof result.then === "function" ? result : chain(result);
    },
    catch() { return next; },
  };
  return next;
}
function response(body) {
  return chain({ json() { return chain(body); } });
}

const ctx = {
  console,
  document: {
    body: fakeEl("body"),
    querySelectorAll() { return []; },
    addEventListener() {},
    getElementById(id) { return getEl(id); },
    createElement() { return fakeEl(); },
  },
  window: {},
  localStorage: {
    getItem(key) { return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null; },
    setItem(key, value) { store[key] = String(value); },
    removeItem(key) { delete store[key]; },
  },
  $: getEl,
  fetch(url, options) {
    const request = options || {};
    requests.push({
      method: request.method || "GET",
      url,
      body: request.body ? JSON.parse(request.body) : null,
    });
    if (url === "/api/overlay") {
      return response({ ok: true, enabled: true, message: "浮窗已开启" });
    }
    return response({ ok: true, message: "saved" });
  },
  toast: noop,
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
  vm.runInContext(match[1], ctx, { filename: "index.html<script>" });
} catch (error) {
  console.error("LOAD FAILED:", error && error.stack || error);
  process.exit(2);
}

const delays = {
  mulligan_ready_delay_seconds: 2,
  mulligan_post_ocr_delay_seconds: 1,
  mulligan_retry_delay_seconds: 1,
  post_action_delay_seconds: 1,
  draw_extra_delay_per_card_seconds: 1.5,
  discover_target_delay_seconds: 1,
  ocr_preprocess_scale: 1,
};
const humanLike = {
  enabled: false,
  hand_hover_enabled: true,
  minion_hover_enabled: false,
  post_delay_min: 0.5,
  post_delay_max: 3,
  hover_min: 0.2,
  hover_max: 1,
  minion_hover_min: 0.2,
  minion_hover_max: 1.5,
};
function status(overrides) {
  return Object.assign({
    ok: true,
    phase: "idle",
    prepared: false,
    overlay_running: false,
    starting: false,
    running: false,
    state: "",
    state_label: "",
    games: 0,
    wins: 0,
    concede_count: 0,
    win_rate: 0,
    config: { name: "", log_root: "" },
    delays,
    human_like: humanLike,
    version: { label: "v2026.10.27" },
  }, overrides || {});
}
function fail(message) { throw new Error(message); }
function assert(condition, message) { if (!condition) fail(message); }

try {
  if (group === "collapse") {
    assert(els.selfcheckBody.style.display === "", "default should be expanded");
    assert(els.btnSelfcheckToggle.textContent === "收起", "default button label");
    els.btnSelfcheckToggle.dispatch("click");
    assert(els.selfcheckBody.style.display === "none", "click should hide body");
    assert(els.btnSelfcheckToggle.textContent === "展开", "click should change label");
    assert(store.hsSelfcheckCollapsed === "1", "collapsed preference should persist");
    els.btnSelfcheckToggle.dispatch("click");
    assert(els.selfcheckBody.style.display === "", "second click should expand");
    assert(store.hsSelfcheckCollapsed === "0", "expanded preference should persist");

    store.hsSelfcheckCollapsed = "1";
    ctx.initSelfcheckCollapse();
    assert(els.selfcheckBody.style.display === "none", "remembered state should collapse");
    ctx.renderSelfcheck({ ok: false, failed: 2, items: [], summary: "bad", python: "3.12", checked_at: "now" });
    assert(els.selfcheckBody.style.display === "", "failure should auto-expand");
    assert(store.hsSelfcheckCollapsed === "1", "failure must not overwrite preference");
  } else if (group === "settings") {
    ctx.render(status());
    assert(els.inpDrawDelay.value == 1.5, "draw delay should render from status");
    assert(els.chkHlHand.checked === true, "hand hover should render true");
    assert(els.chkHlMinion.checked === false, "minion hover should render false");

    els.inpDrawDelay.value = "2.25";
    els.btnSaveDelays.dispatch("click");
    const delayPost = requests.filter((item) => item.method === "POST" && item.url === "/api/delays").pop();
    assert(delayPost && delayPost.body.draw_extra_delay_per_card_seconds === "2.25", "draw delay should submit");

    els.chkHlHand.checked = false;
    els.chkHlMinion.checked = true;
    els.btnSaveHumanLike.dispatch("click");
    const humanPost = requests.filter((item) => item.method === "POST" && item.url === "/api/human_like").pop();
    assert(humanPost && humanPost.body.hand_hover_enabled === false, "hand switch should submit");
    assert(humanPost && humanPost.body.minion_hover_enabled === true, "minion switch should submit");
  } else if (group === "overlay") {
    ctx.render(status({ prepared: false, overlay_running: false, version: null }));
    assert(els.btnStart.textContent === "🪄 开始运行（准备）", "unprepared start label");
    assert(els.btnOverlay.textContent === "🪟 日志浮窗（关）", "overlay off label");
    assert(els.appVersion.textContent === "v—", "missing version placeholder");

    ctx.render(status({ prepared: true, overlay_running: true, version: { label: "v2026.10.27" } }));
    assert(els.btnStart.textContent === "⚔️ 开始对战", "prepared start label");
    assert(els.btnOverlay.textContent === "🪟 日志浮窗（开）", "overlay on label");
    assert(els.appVersion.textContent === "v2026.10.27", "version should render");

    let refreshed = 0;
    ctx.refreshStatus = () => { refreshed += 1; };
    els.btnOverlay.dispatch("click");
    assert(els.btnOverlay.textContent === "🪟 日志浮窗（开）", "toggle should update label");
    assert(refreshed === 1, "toggle should refresh status immediately");
  } else {
    fail("unknown group: " + group);
  }
  console.log("OK: " + group);
} catch (error) {
  console.error("FAIL:", error && error.stack || error);
  process.exit(1);
}
