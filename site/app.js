import { parseAndFormat } from "./logic.js";
const input = document.querySelector("#input");
const output = document.querySelector("#output");
const status = document.querySelector("#status");
const pythonMode = location.pathname.startsWith("/python/");
if (pythonMode) {
  document.querySelector(".menu-bar > span:nth-of-type(2)").textContent =
    "Python edition";
  document.querySelector("#mode-label").textContent =
    "Python lexer/parser active · input is sent to this Flask server.";
  document.querySelector("#mode-short").textContent = "PYTHON SERVER";
  const sourceLink = document.querySelector(".parser-mode a");
  sourceLink.href = "https://github.com/Markkimotho/json-parser#python-version";
  sourceLink.textContent = "Python source & local setup ↗";
  const browserLink = document.createElement("a");
  browserLink.href = "https://markkimotho.github.io/json-parser/";
  browserLink.textContent = "browser edition";
  document
    .querySelector(".footnote")
    .replaceChildren(
      "This edition sends submitted JSON to the Flask server and parses it with the repository's Python lexer/parser. Use the ",
      browserLink,
      " to keep input on your device.",
    );
}

async function parseWithPython(indent) {
  const body = new FormData();
  body.set("jsonData", input.value);
  const response = await fetch("/parse-json", { method: "POST", body });
  const data = await response.json();
  if (!response.ok) {
    const position =
      data.line && data.column
        ? ` (line ${data.line}, column ${data.column})`
        : "";
    throw new Error(`${data.error || "Could not parse JSON"}${position}`);
  }
  return indent ? data.formatted : data.minified;
}

async function run(indent) {
  try {
    const bytes = new Blob([input.value]).size;
    if (bytes > 10 * 1024 * 1024)
      throw new Error("JSON must be 10 MB or smaller");
    if (pythonMode) status.textContent = "Parsing with Python…";
    output.textContent = pythonMode
      ? await parseWithPython(indent)
      : parseAndFormat(input.value, indent);
    status.textContent = `Valid JSON · ${
      pythonMode ? "Python parser" : "browser parser"
    } · ${bytes.toLocaleString()} bytes`;
  } catch (error) {
    output.textContent = "No result";
    status.textContent = `Could not parse JSON · ${error.message}`;
  }
}
document.querySelector("#format").addEventListener("click", () => run(2));
document.querySelector("#minify").addEventListener("click", () => run(0));
document.querySelector("#sample").addEventListener("click", () => {
  input.value = '{"project":"JSON workbench","works":true,"items":[1,2,3]}';
  run(2);
});
document.querySelector("#file").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    status.textContent = "File must be 10 MB or smaller";
    return;
  }
  input.value = await file.text();
  run(2);
});
document.querySelector("#copy").addEventListener("click", async () => {
  if (output.textContent === "No result" || !input.value.trim()) return;
  try {
    await navigator.clipboard.writeText(output.textContent);
    status.textContent = "Result copied";
  } catch {
    status.textContent = "Clipboard unavailable in this browser";
  }
});
