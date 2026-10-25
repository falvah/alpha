"use strict";
const one = (selector) => document.querySelector(selector);
const all = (selector) => document.querySelectorAll(selector);
const menu = one("#menu-toggle");
menu?.addEventListener("click", () => {
  const open = one("#sidebar").classList.toggle("is-open");
  menu.setAttribute("aria-expanded", String(open));
  menu.setAttribute("aria-label", open ? "Закрыть меню" : "Открыть меню");
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    one("#sidebar")?.classList.remove("is-open");
    menu?.setAttribute("aria-expanded", "false");
    menu?.setAttribute("aria-label", "Открыть меню");
  }
});
all(".dismiss-message").forEach((button) => button.addEventListener("click", () => button.closest(".message").remove()));
all("[data-login]").forEach((button) => button.addEventListener("click", () => {
  all("[data-login]").forEach((tab) => { tab.classList.remove("selected"); tab.setAttribute("aria-pressed", "false"); });
  button.classList.add("selected"); button.setAttribute("aria-pressed", "true");
  one("#login-username").value = button.dataset.login;
  one("#login-password").focus();
}));
one(".password-toggle")?.addEventListener("click", (event) => {
  const input = one("#login-password");
  input.type = input.type === "password" ? "text" : "password";
  event.currentTarget.textContent = input.type === "password" ? "Показать" : "Скрыть";
  event.currentTarget.setAttribute("aria-label", input.type === "password" ? "Показать пароль" : "Скрыть пароль");
});
all("form[data-confirm]").forEach((form) => form.addEventListener("submit", (event) => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));
// Only textContent is used for dynamic server values; no HTML injection sinks.
const calculator = one("#calculator-form");
let calculateController;
let calculationRevision = 0;
function invalidateCalculation() {
  calculationRevision += 1;
  calculateController?.abort();
  const apply = one("#calc-apply");
  apply.removeAttribute("href");
  apply.setAttribute("aria-disabled", "true");
  ["payment", "total", "rate"].forEach(name => { one(`#calc-${name}`).textContent = "—"; });
  one("#calc-error").textContent = "Параметры изменены. Нажмите «Рассчитать платёж».";
}
async function calculate(event) {
  event?.preventDefault();
  invalidateCalculation();
  if (!calculator.reportValidity()) return;
  calculateController = new AbortController();
  const revision = calculationRevision;
  const params = new URLSearchParams(new FormData(calculator));
  const error = one("#calc-error");
  error.textContent = "";
  try {
    const response = await fetch(`/api/v1/calculator/?${params}`, {signal: calculateController.signal, credentials: "same-origin", headers: {Accept: "application/json"}});
    const data = await response.json();
    if (revision !== calculationRevision) return;
    if (!response.ok) throw new Error(data.error || "Не удалось рассчитать платёж. Войдите в кабинет ещё раз.");
    const currency = new Intl.NumberFormat("ru-RU", {style: "currency", currency: "RUB", maximumFractionDigits: 0});
    one("#calc-payment").textContent = currency.format(Number(data.payment));
    one("#calc-total").textContent = currency.format(Number(data.total));
    one("#calc-rate").textContent = `${Number(data.rate).toLocaleString("ru-RU")}% годовых`;
    one("#calc-apply").href = `/applications/new/?${params}`;
    one("#calc-apply").removeAttribute("aria-disabled");
  } catch (failure) {
    if (failure.name !== "AbortError" && revision === calculationRevision) {
      one("#calc-payment").textContent = "—";
      one("#calc-total").textContent = "—";
      one("#calc-rate").textContent = "—";
      error.textContent = failure.message || "Не удалось связаться с сервером. Повторите расчёт.";
    }
  }
}
if (calculator) {
  calculator.addEventListener("input", invalidateCalculation);
  calculator.addEventListener("change", invalidateCalculation);
  one("#calc-apply").addEventListener("click", event => {
    if (one("#calc-apply").getAttribute("aria-disabled") === "true") event.preventDefault();
  });
  calculator.addEventListener("submit", calculate);
  one("#calc-product").addEventListener("change", () => {
    const option = one("#calc-product").selectedOptions[0];
    const amount = calculator.elements.amount, months = calculator.elements.months;
    amount.min = option.dataset.min; amount.max = option.dataset.max;
    months.max = option.dataset.months;
    amount.value = Math.max(Number(amount.min), Math.min(Number(amount.value), Number(amount.max)));
    months.value = Math.min(Number(months.value), Number(months.max));
    // Let the bubbling change handler invalidate before starting the new request.
    queueMicrotask(() => calculate());
  });
  calculate();
}
const decision = one("#id_decision");
if (decision) {
  function toggleOffer() {
    ["amount", "months", "rate"].forEach((name) => {
      const field = one(`#id_${name}`);
      if (!field) return;
      const show = decision.value === "counteroffer";
      field.closest(".field").classList.toggle("inline-hidden", !show);
      field.required = show;
      field.disabled = !show;
    });
  }
  decision.addEventListener("change", toggleOffer); toggleOffer();
}
