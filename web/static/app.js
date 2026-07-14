// Отправка брифа на ядро. Форма помечена data-variant="individual|community".

function showResult(node, kind, message) {
  if (!node) return;
  node.textContent = message;
  node.className = "result show " + kind;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
}

function markMissing(form, missing) {
  for (const key of missing) {
    const names = key === "contact" ? ["email", "phone", "telegram"] : [key];
    for (const name of names) {
      const el = form.querySelector('[name="' + name + '"]');
      if (el && el.closest(".field")) el.closest(".field").classList.add("invalid");
    }
  }
}

async function submitBrief(form) {
  const variant = form.dataset.variant;
  const payload = {};
  for (const el of form.querySelectorAll("[name]")) {
    payload[el.name] = (el.value || "").trim();
  }
  form.querySelectorAll(".field.invalid").forEach((f) => f.classList.remove("invalid"));
  const result = document.querySelector(".result");
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;
  const params = new URLSearchParams(location.search);
  const refCode = params.get("ref");
  // Токен инвайта из ссылки оператора (?t=...): связывает бриф с инвайтом.
  const token = params.get("t");
  try {
    const resp = await fetch("/api/v1/briefs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant: variant, payload: payload, ref_code: refCode, token: token }),
    });
    if (resp.status === 201) {
      showResult(result, "ok", "Бриф отправлен. Спасибо — мы свяжемся с вами.");
      form.reset();
    } else if (resp.status === 422) {
      const data = await resp.json();
      const missing = (data.detail && data.detail.missing) || [];
      markMissing(form, missing);
      showResult(result, "err", "Заполните обязательные поля, отмеченные красным.");
    } else if (resp.status === 404) {
      showResult(result, "err", "Ссылка недействительна. Запросите новую у менеджера.");
    } else if (resp.status === 409) {
      showResult(result, "err", "Этот бриф уже отправлен. Повторно заполнять не нужно.");
    } else if (resp.status === 429) {
      showResult(result, "err", "Слишком много попыток. Подождите минуту и повторите.");
    } else {
      showResult(result, "err", "Не удалось отправить. Попробуйте позже.");
    }
  } catch (e) {
    showResult(result, "err", "Нет связи с сервером. Проверьте интернет и попробуйте снова.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form instanceof HTMLFormElement && form.matches("form[data-variant]")) {
    event.preventDefault();
    submitBrief(form);
  }
});
