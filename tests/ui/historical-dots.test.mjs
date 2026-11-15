import { JSDOM, PAGE, ui } from "./assets.mjs";

const script = ui("historical.js");

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, what) {
  const end = Date.now() + 4000;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out: " + what);
    await sleep(20);
  }
}

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  url: "https://frigate.invalid/", pretendToBeVisual: true, runScripts: "dangerously",
});
const window = dom.window;
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

// Server-side catalog per camera; tests mutate it between selections.
const catalog = { camA: ["DAY10"], camB: ["DAY12"] };
const statusCalls = [];
const holds = new Map();
window.fetch = async (path) => {
  if (path === "/api/config") return new Response(JSON.stringify({ui:{timezone:"UTC"}}), {status:200,headers:{"Content-Type":"application/json"}});
  if (path === "/__nanexus/session") {
    return json(200, { principal: "viewer", operations_available: true,
      cameras: [{ camera_id: "camA", display_name: "A" }, { camera_id: "camB", display_name: "B" }] });
  }
  if (path === "/api/version") return new Response("0.18.2-cfa08c5", { status: 200, headers: { "Content-Type": "text/plain" } });
  if (String(path).startsWith("/__nanexus/window/status")) {
    const query = new URL(path, "https://frigate.invalid").searchParams;
    const camera = query.get("camera"), month = query.get("month"), zone = query.get("zone");
    statusCalls.push(camera);
    if (holds.has(camera)) await holds.get(camera);
    const dates = catalog[camera].map((d) => month + "-" + d.slice(3));
    return json(200, { schema: "nanexus-hv2-catalog-days/1", camera, month, zone, dates, complete: true, meaning: "catalog_recordings_present" });
  }
  throw new Error("unexpected " + path);
};

const source = window.document.createElement("script");
source.textContent = script;
window.document.body.appendChild(source);
const root = window.document.getElementById("root");
const unmount = window.__nanexusHistorical.mount(root);
await until(() => root.querySelector("select option[value=camB]"), "session");
const camera = root.querySelector("select[aria-label=Camera]");
const marked = () => [...root.querySelectorAll("button.nx-has-recording")].map((b) => b.textContent.replace(/\D/g, "")).sort().join(",");
const choose = (value) => { camera.value = value; camera.dispatchEvent(new window.Event("change", { bubbles: true })); };

await until(() => statusCalls.length >= 1, "initial dots");
await sleep(200);

// Camera selection fetches current availability for that camera.
choose("camA");
await until(() => marked() === "10", "camA dots");
await sleep(300);

// Start/End changes never refresh inventory.
const before = statusCalls.length;
for (const [label, value] of [["Start hour", "10"], ["Start minute", "05"], ["End hour", "10"], ["End minute", "20"]]) {
  const select = root.querySelector(`select[aria-label="${label}"]`);
  select.value = value;
  select.dispatchEvent(new window.Event("change", { bubbles: true }));
}
await sleep(300);
if (statusCalls.length !== before) throw new Error("Start/End changed inventory: " + statusCalls.slice(before));

// Switch while a camA response is still in flight: dots clear at once and the
// late camA answer never paints over camB.
let releaseA;
holds.set("camA", new Promise((resolve) => { releaseA = resolve; }));
choose("camA");
await until(() => statusCalls.length === before + 1, "held camA request");
choose("camB");
if (marked() !== "") throw new Error("stale dots stayed after camera change: " + marked());
await until(() => marked() === "12", "camB dots");
releaseA();
holds.delete("camA");
await sleep(200);
if (marked() !== "12") throw new Error("late camA response leaked: " + marked());

// A recording added server-side shows up on the next selection, no reload.
catalog.camA = ["DAY10", "DAY11"];
choose("camA");
await until(() => marked() === "10,11", "new recording date");
const afterStartEnd = statusCalls.slice(before);
if (afterStartEnd.join(",") !== "camA,camB,camA") throw new Error("unexpected status fetches " + afterStartEnd);

unmount();
window.close();
console.log("historical-dots: pass");
