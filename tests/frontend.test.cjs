const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element(extra = {}) {
  const attrs = new Map();
  return {
    textContent: "", value: "", disabled: false, required: false, listeners: {},
    get href() { return attrs.get("href") || ""; },
    set href(value) { attrs.set("href", value); },
    setAttribute(key, value) { attrs.set(key, value); },
    getAttribute(key) { return attrs.get(key) ?? null; },
    removeAttribute(key) { attrs.delete(key); },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    ...extra,
  };
}

async function setup() {
  const amount = element({ value: "1500000" });
  const months = element({ value: "24" });
  const product = element({ value: "working" });
  const calculator = element({ elements: { amount, months, product }, valid: true, reportValidity() { return this.valid; } });
  const decision = element({ value: "counteroffer" });
  const fields = Object.fromEntries(["amount", "months", "rate"].map(name => {
    const field = element({ hidden: false });
    field.closest = () => ({ classList: { toggle: (_, hidden) => { field.hidden = hidden; } } });
    return [name, field];
  }));
  const nodes = {
    "#calculator-form": calculator, "#calc-product": product, "#id_decision": decision,
    ...Object.fromEntries(["payment", "total", "rate", "error", "apply"].map(name => [`#calc-${name}`, element()])),
    ...Object.fromEntries(Object.entries(fields).map(([name, field]) => [`#id_${name}`, field])),
  };
  const state = { calls: 0, fetch: async () => response("79306.65") };
  const context = vm.createContext({
    document: { querySelector: selector => nodes[selector] || null, querySelectorAll: () => [], addEventListener() {} },
    AbortController, URLSearchParams, Intl, queueMicrotask,
    FormData: class {
      constructor(form) { this.entries = Object.entries(form.elements).map(([key, field]) => [key, field.value]); }
      [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
    },
    fetch: (...args) => { state.calls++; return state.fetch(...args); },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/js/app.js"), "utf8"), context);
  await new Promise(resolve => setImmediate(resolve));
  return { context, state, nodes, calculator, decision, fields, amount };
}

function response(payment) {
  return { ok: true, json: async () => ({ payment, total: "1903359.55", rate: "24" }) };
}

test("changing inputs clears the previous loan link and payment", async () => {
  const { nodes, calculator, amount } = await setup();
  assert.match(nodes["#calc-apply"].href, /amount=1500000/);
  amount.value = "2000000";
  calculator.listeners.input();
  assert.equal(nodes["#calc-apply"].getAttribute("href"), null);
  assert.equal(nodes["#calc-payment"].textContent, "—");
  assert.equal(nodes["#calc-apply"].getAttribute("aria-disabled"), "true");
});

test("invalid fields do not send a request or keep an actionable result", async () => {
  const { context, state, nodes, calculator } = await setup();
  const calls = state.calls;
  calculator.valid = false;
  await context.calculate();
  assert.equal(state.calls, calls);
  assert.equal(nodes["#calc-apply"].getAttribute("href"), null);
});

test("a failed connection clears results and shows an error", async () => {
  const { context, state, nodes } = await setup();
  state.fetch = async () => { throw new Error("Соединение потеряно"); };
  await context.calculate();
  assert.equal(nodes["#calc-apply"].getAttribute("href"), null);
  assert.equal(nodes["#calc-error"].textContent, "Соединение потеряно");
});

test("a delayed previous response cannot overwrite a newer calculation", async () => {
  const { context, state, nodes } = await setup();
  let finishPrevious;
  state.fetch = () => new Promise(resolve => { finishPrevious = resolve; });
  const previous = context.calculate();
  state.fetch = async () => response("125000");
  await context.calculate();
  const current = nodes["#calc-payment"].textContent;
  finishPrevious(response("1"));
  await previous;
  assert.equal(nodes["#calc-payment"].textContent, current);
  assert.match(current, /125/);
});

test("switching a decision disables hidden counteroffer fields", async () => {
  const { decision, fields } = await setup();
  decision.value = "approved";
  decision.listeners.change();
  for (const field of Object.values(fields)) {
    assert.equal(field.hidden, true);
    assert.equal(field.disabled, true);
    assert.equal(field.required, false);
  }
  decision.value = "counteroffer";
  decision.listeners.change();
  for (const field of Object.values(fields)) {
    assert.equal(field.disabled, false);
    assert.equal(field.required, true);
  }
});
