/* Small no-dependency interactions. No analytics, network calls or storage. */
"use strict";
document.addEventListener("click", async (event) => {
  const trigger = event.target.closest("[data-copy-link]");
  if (!trigger) return;
  const link = trigger.closest(".link-box")?.querySelector("code")?.textContent?.trim();
  if (!link) return;
  try {
    await navigator.clipboard.writeText(link);
    const old = trigger.textContent;
    trigger.textContent = "کپی شد ✓";
    trigger.setAttribute("aria-live", "polite");
    setTimeout(() => { trigger.textContent = old; }, 1300);
  } catch {
    trigger.textContent = "کپی نشد؛ لینک را انتخاب کنید";
  }
});

document.addEventListener("input", (event) => {
  const search = event.target.closest("[data-filter-table]");
  if (!search) return;
  const table = document.getElementById(search.dataset.filterTable);
  if (!table) return;
  const query = search.value.trim().toLocaleLowerCase();
  table.querySelectorAll("tbody tr").forEach((row) => {
    const rowText = row.textContent.toLocaleLowerCase();
    row.hidden = !rowText.includes(query);
  });
});
