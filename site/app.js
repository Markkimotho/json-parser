import { parseAndFormat } from "./logic.js";
const input = document.querySelector("#input");
const output = document.querySelector("#output");
const status = document.querySelector("#status");
function run(indent) {
  try {
    output.textContent = parseAndFormat(input.value, indent);
    status.textContent = `Valid JSON · ${new Blob([input.value]).size.toLocaleString()} bytes`;
  } catch (error) { output.textContent = "No result"; status.textContent = `Invalid JSON · ${error.message}`; }
}
document.querySelector("#format").addEventListener("click", () => run(2));
document.querySelector("#minify").addEventListener("click", () => run(0));
document.querySelector("#sample").addEventListener("click", () => { input.value = '{"project":"JSON workbench","works":true,"items":[1,2,3]}'; run(2); });
document.querySelector("#file").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { status.textContent = "File must be 10 MB or smaller"; return; }
  input.value = await file.text(); run(2);
});
document.querySelector("#copy").addEventListener("click", async () => {
  if (output.textContent === "No result" || !input.value.trim()) return;
  try { await navigator.clipboard.writeText(output.textContent); status.textContent = "Result copied"; }
  catch { status.textContent = "Clipboard unavailable in this browser"; }
});
