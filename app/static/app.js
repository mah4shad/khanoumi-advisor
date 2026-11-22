const thread = document.querySelector("#thread");
const form = document.querySelector("#composer");
const messageBox = document.querySelector("#message");
const sendButton = document.querySelector("#send");
const chips = document.querySelector("#chips");
const compareBar = document.querySelector("#compare-bar");
const compareCount = document.querySelector("#compare-count");
const shelf = document.querySelector("#shelf");

const selected = new Map();
let sessionId = sessionStorage.getItem("advisor-session") || "";
let busy = false;

const starters = [
  "پوستم چرب است و جوش می‌زند. یک روتین ساده پیشنهاد بده.",
  "پوست خشک و حساس دارم. مرطوب‌کننده و شوینده ملایم می‌خواهم.",
  "ضدآفتاب روزمره برای پوست مختلط، زیر ۵۰۰ هزار تومان.",
  "دو مرطوب‌کننده مناسب لک را با هم مقایسه کن.",
];

starters.forEach((text) => {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chip";
  button.textContent = text;
  button.addEventListener("click", () => {
    messageBox.value = text;
    form.requestSubmit();
  });
  chips.appendChild(button);
});

const greeting =
  "سلام. نوع پوست، نیاز و بودجه‌ات را بگو تا از بین محصولات موجود خانومی چند گزینه با تصویر، قیمت و لینک خرید بچینم و یک روتین کوتاه هم بنویسم.";
addBubble("bot", greeting);

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    const shelfOn = tab.dataset.view === "shelf";
    document.querySelector("#chat-view").classList.toggle("hidden", shelfOn);
    document.querySelector("#shelf-view").classList.toggle("hidden", !shelfOn);
    if (shelfOn && !shelf.dataset.loaded) loadShelf();
  });
});

document.querySelector("#new-chat").addEventListener("click", () => {
  if (busy) return;
  sessionId = "";
  sessionStorage.removeItem("advisor-session");
  selected.clear();
  renderCompareBar();
  thread.replaceChildren();
  addBubble("bot", greeting);
});

document.querySelector("#reload-shelf").addEventListener("click", () => loadShelf());
document.querySelector("#compare-clear").addEventListener("click", () => {
  selected.clear();
  document.querySelectorAll(".pick").forEach((box) => {
    box.checked = false;
  });
  renderCompareBar();
});
document.querySelector("#compare-go").addEventListener("click", () => {
  if (selected.size < 2) return;
  ask("این محصولات را از نظر قیمت، کاربرد و مناسب بودن برای پوست من مقایسه کن.", [...selected.keys()]);
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(messageBox.value, []);
});

messageBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

async function ask(text, compareIds) {
  const message = text.trim();
  if (message.length < 2 || busy) return;
  busy = true;
  sendButton.disabled = true;
  addBubble("user", message);
  messageBox.value = "";
  const pending = addBubble("bot", "دارم محصولات موجود خانومی را با بودجه و نوع پوستت چک می‌کنم…");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId || null,
        message,
        compare_ids: compareIds,
        profile: profile(),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "پاسخ نگرفتم");
    sessionId = data.session_id;
    sessionStorage.setItem("advisor-session", sessionId);
    pending.remove();
    renderAnswer(data);
  } catch (error) {
    pending.classList.add("error");
    pending.textContent = error.message || "ارتباط قطع شد.";
  } finally {
    busy = false;
    sendButton.disabled = false;
    messageBox.focus();
  }
}

function profile() {
  const budget = toAscii(document.querySelector("#budget").value).replace(/[^\d]/g, "");
  return {
    skin_type: document.querySelector("#skin").value,
    concerns: [...document.querySelectorAll(".check input:checked")].map((input) => input.value),
    budget: budget ? Number(budget) : null,
  };
}

function renderAnswer(data) {
  const bubble = addBubble("bot", data.message);
  if (data.products?.length) bubble.append(productList(data.products));
  if (data.routine?.length) bubble.append(routineBlock(data.routine, data.products || []));
  if (data.comparison) bubble.append(comparisonBlock(data.comparison));
  const note = document.createElement("p");
  note.className = "meta";
  note.textContent = `از ${data.catalog_size} محصول موجود فروشگاه بررسی شد.`;
  bubble.append(note);
}

function productList(products) {
  const wrap = document.createElement("div");
  wrap.className = "products";
  products.forEach((product) => wrap.append(productCard(product)));
  return wrap;
}

function productCard(product) {
  const card = document.createElement("article");
  card.className = "product";
  const img = document.createElement("img");
  img.alt = product.title || "";
  img.loading = "lazy";
  img.addEventListener("error", () => img.classList.add("broken"));
  img.src = product.image || "";
  const body = document.createElement("div");
  const role = document.createElement("div");
  role.className = "role";
  role.textContent = product.role || product.brand || "";
  const title = document.createElement("h3");
  title.textContent = product.title;
  const brand = document.createElement("div");
  brand.className = "brand";
  brand.textContent = product.brand || "";
  const price = document.createElement("div");
  price.className = "price";
  price.append(document.createTextNode(toman(product.final_price)));
  if (product.discount_pct) {
    const base = document.createElement("s");
    base.textContent = toman(product.price);
    price.append(base);
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `${product.discount_pct}٪`;
    price.append(badge);
  }
  const stock = document.createElement("span");
  stock.className = product.in_stock ? "badge" : "badge out";
  stock.textContent = product.in_stock ? "موجود" : "ناموجود";
  price.append(stock);
  const why = document.createElement("p");
  why.textContent = product.why || "";
  const actions = document.createElement("div");
  actions.className = "actions";
  const link = document.createElement("a");
  link.href = product.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "خرید از خانومی";
  const label = document.createElement("label");
  label.className = "pick-label";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "pick";
  box.checked = selected.has(product.id);
  box.addEventListener("change", () => {
    if (box.checked && selected.size >= 4) {
      box.checked = false;
      return;
    }
    if (box.checked) selected.set(product.id, product.title);
    else selected.delete(product.id);
    renderCompareBar();
  });
  label.append(box, document.createTextNode(" مقایسه"));
  actions.append(link, label);
  body.append(role, title, brand, price, why, actions);
  card.append(img, body);
  return card;
}

function routineBlock(steps, products) {
  const wrap = document.createElement("div");
  wrap.className = "routine";
  const names = Object.fromEntries(products.map((item) => [item.id, item.title]));
  const rank = (step) => (step.time === "شب" ? 1 : 0);
  steps
    .slice()
    .sort((a, b) => rank(a) - rank(b) || a.order - b.order)
    .forEach((step) => {
      const row = document.createElement("article");
      row.className = "step";
      const when = document.createElement("div");
      when.className = "when";
      when.textContent = `${step.time} ${Number(step.order).toLocaleString("fa-IR")}`;
      const body = document.createElement("div");
      const title = document.createElement("h3");
      title.textContent = step.title;
      const detail = document.createElement("p");
      detail.textContent = step.detail;
      body.append(title, detail);
      if (step.product_id && names[step.product_id]) {
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = names[step.product_id];
        body.append(meta);
      }
      row.append(when, body);
      wrap.append(row);
    });
  return wrap;
}

function comparisonBlock(comparison) {
  const wrap = document.createElement("div");
  wrap.className = "comparison";
  const summary = document.createElement("p");
  summary.textContent = comparison.summary;
  wrap.append(summary);
  comparison.points.forEach((point) => {
    const row = document.createElement("article");
    row.className = "point";
    const title = document.createElement("h3");
    title.textContent = point.title;
    const pros = document.createElement("p");
    pros.textContent = `مزیت: ${point.pros}`;
    const cons = document.createElement("p");
    cons.textContent = `محدودیت: ${point.cons}`;
    row.append(title, pros, cons);
    wrap.append(row);
  });
  return wrap;
}

function renderCompareBar() {
  compareBar.hidden = selected.size === 0;
  compareCount.textContent = `${selected.size.toLocaleString("fa-IR")} محصول برای مقایسه`;
  document.querySelector("#compare-go").disabled = selected.size < 2;
}

function addBubble(kind, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${kind}`;
  bubble.textContent = text;
  thread.append(bubble);
  thread.scrollTop = thread.scrollHeight;
  return bubble;
}

async function loadShelf() {
  shelf.replaceChildren();
  shelf.dataset.loaded = "1";
  const waiting = document.createElement("p");
  waiting.textContent = "در حال خواندن ویترین خانومی…";
  shelf.append(waiting);
  try {
    const response = await fetch("/api/showcase");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "ویترین باز نشد");
    shelf.replaceChildren();
    data.products.forEach((product) => {
      const card = document.createElement("article");
      card.className = "shelf-card";
      const img = document.createElement("img");
      img.alt = product.title || "";
      img.loading = "lazy";
      img.addEventListener("error", () => img.classList.add("broken"));
      img.src = product.image || "";
      const brand = document.createElement("div");
      brand.className = "brand";
      brand.textContent = product.brand || "";
      const title = document.createElement("h3");
      title.textContent = product.title;
      const price = document.createElement("div");
      price.className = "price";
      price.textContent = toman(product.final_price);
      const link = document.createElement("a");
      link.href = product.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = product.in_stock ? "مشاهده و خرید" : "ناموجود";
      card.append(img, brand, title, price, link);
      shelf.append(card);
    });
  } catch (error) {
    waiting.textContent = error.message;
  }
}

function toman(value) {
  if (value == null) return "قیمت نامشخص";
  return `${Number(value).toLocaleString("fa-IR")} تومان`;
}

function toAscii(text) {
  return String(text || "")
    .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));
}
