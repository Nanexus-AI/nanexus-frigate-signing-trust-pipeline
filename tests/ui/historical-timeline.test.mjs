import { JSDOM, PAGE, ui } from "./assets.mjs";

const css = ui("navigation.css").toString("utf8");
const script = ui("historical.js");

const REQUEST = "0123456789abcdef0123456789abcdef";
const files = [
  { file_id: "a", recording: "rec-a", position: 0, metadata_interval: { start: "100", end: "110" }, state: "COMPLETED", finding: "AMBER" },
  { file_id: "b", recording: "rec-b", position: 1, metadata_interval: { start: "130", end: "140" }, state: "COMPLETED", finding: "GREEN" },
  { file_id: "c", recording: "rec-c", position: 2, metadata_interval: { start: "150", end: "160" }, state: "COMPLETED", finding: "RED" },
];
const progress = {
  schema: "nanexus-hv2-window-progress/1",
  request_id: REQUEST,
  camera: "cam1",
  state: "COMPLETE",
  reason: null,
  files,
  summary: null,
  summary_only: true,
  evidence_available: false,
  origin: "HOLD",
  continuity: "UNKNOWN",
  coverage: "UNKNOWN",
  catalog_gaps_state: "available",
  catalog_gaps: [{ type: "GAP", start: "110", end: "130", duration: "20", camera: "cam1", gap_id: "gap-1" }],
  query_coverage: {
    schema: "nanexus-hv2-query-coverage/1",
    query: { start: "100", end: "170" },
    dataset: { start: "90", end: "180" },
    segments: [
      { kind: "recording", start: "100", end: "110" },
      { kind: "gap", start: "110", end: "130" },
      { kind: "outside", start: "130", end: "170" },
    ],
  },
};

function responseFor(body, text) {
  return new Response(text ? body : JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": text ? "text/plain" : "application/json" },
  });
}

const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div><style>${css}</style></body></html>`, {
  url: "https://frigate.invalid/",
  pretendToBeVisual: true,
  runScripts: "dangerously",
});
const window = dom.window;
window.fetch = async (path) => {
  if (path === "/api/config") return new Response(JSON.stringify({ui:{timezone:"UTC"}}), {status:200,headers:{"Content-Type":"application/json"}});
  if (path === "/__nanexus/session") {
    return responseFor({ principal: "admin", cameras: [{ camera_id: "cam1", display_name: "cam1" }], operations_available: true });
  }
  if (path === "/api/version") return responseFor("0.18.2-cfa08c5", true);
  if (String(path).startsWith("/__nanexus/window/status")) {
    return responseFor({ schema: "nanexus-hv2-catalog-days/1", camera: "cam1", month: "2026-10", zone: "UTC", dates: ["2026-10-26"], complete: true, meaning: "catalog_recordings_present" });
  }
  if (path === "/__nanexus/window/verify") return responseFor({ request_id: REQUEST, state: "RUNNING" });
  if (String(path).startsWith("/__nanexus/window/progress")) return responseFor(progress);
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

const coverage = window.document.getElementById("historical-coverage");
const grid = root.querySelector(".nx-file-grid");
const until = Date.now() + 4000;
while (coverage.hidden) {
  if (Date.now() > until) throw new Error("accepted timeline did not render");
  await new Promise((resolve) => setTimeout(resolve, 20));
}
if (grid && !grid.hidden) throw new Error("deprecated per-file grid was displayed");
if (root.querySelector(".nx-file-block") || root.querySelector(".nx-gap-block")) {
  throw new Error("ordinal file or catalog-gap strip was drawn");
}
if (coverage.querySelectorAll(".nx-cov-gap").length !== 1 || coverage.querySelectorAll(".nx-cov-outside").length !== 1) {
  throw new Error("query_coverage fixture was not drawn on the timeline");
}
if (coverage.querySelectorAll(".nx-cov-amber").length !== 1 || coverage.querySelectorAll(".nx-cov-green").length !== 1 || coverage.querySelectorAll(".nx-cov-red").length !== 1) {
  throw new Error("whole-file finding colors were not painted on the timeline");
}
unmount();
console.log("historical-timeline: pass");
