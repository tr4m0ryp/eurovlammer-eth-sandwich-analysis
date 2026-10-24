// Caption banner and title cards drawn into the recorded page. Text only describes what is on
// screen; every number on a card is read from the running screeners' /api/snapshot.

const CARD_CSS = `
  body { margin: 0; height: 100vh; display: grid; place-items: center; background: #0d1117;
         color: #e6edf3; font: 400 22px/1.45 Geist, system-ui, sans-serif; }
  .card { width: 1180px; display: grid; gap: 18px; }
  h1 { margin: 0; font: 600 52px/1.1 Geist, system-ui, sans-serif; letter-spacing: -.02em; }
  .sub { color: #8b949e; font-size: 24px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 10px; }
  .k { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 18px 20px; }
  .k b { display: block; font: 600 34px/1.2 "Geist Mono", ui-monospace, monospace; color: #4493f8; }
  .k span { color: #8b949e; font-size: 17px; }
  .note { color: #f0883e; font-size: 18px; margin-top: 8px; }`;

export function cardHtml({ title, sub, stats = [], note = "" }) {
  const tiles = stats.map(([v, l]) => `<div class="k"><b>${v}</b><span>${l}</span></div>`).join("");
  return `<!doctype html><html><head><meta charset="utf-8">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;600&family=Geist+Mono:wght@600&display=swap">
    <style>${CARD_CSS}</style></head><body><div class="card"><h1>${title}</h1>
    <div class="sub">${sub}</div>${tiles ? `<div class="grid">${tiles}</div>` : ""}
    ${note ? `<div class="note">${note}</div>` : ""}</div></body></html>`;
}

export async function caption(page, text) {
  await page.evaluate((t) => {
    let el = document.getElementById("rec-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "rec-caption";
      Object.assign(el.style, {
        position: "fixed", left: "50%", bottom: "28px", transform: "translateX(-50%)",
        maxWidth: "1300px", padding: "12px 22px", borderRadius: "10px", zIndex: 99999,
        background: "rgba(13,17,23,.92)", color: "#e6edf3", font: "500 19px/1.4 system-ui, sans-serif",
        boxShadow: "0 8px 24px rgba(0,0,0,.25)", textAlign: "center",
      });
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

export async function clickTab(page, name) {
  await page.click(`#tabs .tab[data-tab="${name}"]`);
}
