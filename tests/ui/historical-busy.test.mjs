import { JSDOM, PAGE, ui } from "./assets.mjs";

const script = ui("historical.js");

function responseFor(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function busyMessage(running) {
  const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
    url: "https://frigate.invalid/",
    pretendToBeVisual: true,
    runScripts: "dangerously",
  });
  const window = dom.window;
  let verifies = 0;
  window.fetch = async (path) => {
  if (path === "/api/config") return new Response(JSON.stringify({ui:{timezone:"UTC"}}), {status:200,headers:{"Content-Type":"application/json"}});
    if (path === "/__nanexus/session") {
      return responseFor(200, { principal: "admin", cameras: [{ camera_id: "cam1", display_name: "cam1" }], operations_available: true });
    }
    if (path === "/api/version") return new Response("0.18.2-cfa08c5", { status: 200, headers: { "Content-Type": "text/plain" } });
    if (String(path).startsWith("/__nanexus/window/status")) {
      return responseFor(200, { schema: "nanexus-hv2-catalog-days/1", camera: "cam1", month: "2026-10", zone: "UTC", dates: ["2026-10-26"], complete: true, meaning: "catalog_recordings_present" });
    }
    if (path === "/__nanexus/window/verify") {
      verifies += 1;
      return responseFor(409, { error: "window_operation_unavailable", running });
    }
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
  const expected = running === true
    ? "A verification is still running. Please wait until it finishes."
    : "Previous verification is finishing. Please try again in a few seconds.";
  const status = window.document.getElementById("historical-state");
  const until = Date.now() + 4000;
  while (status.textContent !== expected) {
    if (Date.now() > until) throw new Error("status was " + JSON.stringify(status.textContent));
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  if (status.textContent.includes("HTTP 409")) throw new Error("generic conflict text remained");
  if (!root.querySelector("button.nx-primary") || root.querySelector("button.nx-primary").textContent !== "Verify") throw new Error("Verify button changed");
  await new Promise((resolve) => setTimeout(resolve, 50));
  if (verifies !== 1) throw new Error("verify was retried " + verifies);
  unmount();
  window.close();
}

await busyMessage(true);
await busyMessage(false);
console.log("historical-busy: pass");
