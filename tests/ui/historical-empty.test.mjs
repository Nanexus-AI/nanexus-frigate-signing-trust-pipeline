import { JSDOM, PAGE, ui } from "./assets.mjs";

const script = ui("historical.js");

const REQUEST = "0123456789abcdef0123456789abcdef";
const MESSAGE = "No retained recordings in the selected time range.";
const empty = {
  schema: "nanexus-hv2-window-progress/1",
  request_id: REQUEST,
  camera: "cam1",
  state: "NO_MATCHING_RECORDING",
  reason: MESSAGE,
  files: [],
  summary: null,
  summary_only: true,
  evidence_available: false,
  origin: "HOLD",
  continuity: "UNKNOWN",
  coverage: "UNKNOWN",
  catalog_gaps: null,
  catalog_gaps_state: "unavailable",
};

function responseFor(body, text) {
  return new Response(text ? body : JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": text ? "text/plain" : "application/json" },
  });
}

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  url: "https://frigate.invalid/",
  pretendToBeVisual: true,
  runScripts: "dangerously",
});
const window = dom.window;
window.fetch = async (path) => {
  if (path === "/__nanexus/session") {
    return responseFor({ principal: "admin", cameras: [{ camera_id: "cam1", display_name: "cam1" }], operations_available: true });
  }
  if (path === "/api/version") return responseFor("0.18.2-cfa08c5", true);
  if (String(path).startsWith("/__nanexus/window/status")) {
    return responseFor({ schema: "nanexus-hv2-catalog-days/1", camera: "cam1", month: "2026-10", zone: "UTC", dates: ["2026-10-26"], complete: true, meaning: "catalog_recordings_present" });
  }
  if (path === "/__nanexus/window/verify") return responseFor({ request_id: REQUEST, state: "RUNNING" });
  if (String(path).startsWith("/__nanexus/window/progress")) return responseFor(empty);
  if (path === "/__nanexus/window/release") return responseFor({ request_id: REQUEST, released: true });
  throw new Error("unexpected " + path);
};
if (typeof window.Blob.prototype.text !== "function") {
  window.Blob.prototype.text = function text() {
    return new Promise((resolve, reject) => {
      const reader = new window.FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}
const source = window.document.createElement("script");
source.textContent = script;
window.document.body.appendChild(source);

const root = window.document.getElementById("root");
const unmount = window.__nanexusHistorical.mount(root);
const start = Date.now();
while (!root.querySelector("select option[value=cam1]")) {
  if (Date.now() - start > 4000) throw new Error("session did not load");
  await new Promise((resolve) => setTimeout(resolve, 20));
}
root.querySelector("select[aria-label=Camera]").value = "cam1";
const date = root.querySelector("input[aria-label=Date]");
date.value = "2026-10-26";
date.dispatchEvent(new window.Event("input", { bubbles: true }));
for (const [label, value] of [["Start hour", "21"], ["Start minute", "50"], ["End hour", "21"], ["End minute", "51"]]) {
  const select = root.querySelector(`select[aria-label="${label}"]`);
  select.value = value;
  select.dispatchEvent(new window.Event("change", { bubbles: true }));
}
root.querySelector("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));

const status = window.document.getElementById("historical-state");
const until = Date.now() + 4000;
while (status.textContent !== MESSAGE) {
  if (Date.now() > until) throw new Error("status was " + JSON.stringify(status.textContent));
  await new Promise((resolve) => setTimeout(resolve, 20));
}
if (status.className === "nx-error") throw new Error("empty retained window was drawn as an error");
if (status.textContent.includes("0/0") || status.textContent.includes("GAP")) throw new Error("empty window used file or gap wording");
if (root.querySelector(".nx-gap-block")) throw new Error("empty window created a catalog gap marker");
if (root.querySelector(".nx-file-block")) throw new Error("empty window created a file strip");
if (!window.document.getElementById("historical-coverage").hidden) throw new Error("empty window drew query coverage");
unmount();
console.log("historical-empty: pass");
