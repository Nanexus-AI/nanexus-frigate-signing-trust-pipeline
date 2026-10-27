import { createHash } from "node:crypto";
import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM, PAGE, ui } from "./assets.mjs";

const script = ui("navigation.js");
const LANDING = "062bb00068baf1304474c3354bb949aa27a15ddce1dfdb869a0e5450f4eca560";

function load() {
  const dom = new JSDOM(
    `<!doctype html><html><body>
      <aside style="position:fixed;left:0;top:0;width:52px;height:800px">
        <a href="/">Live</a><a href="/review">Review</a>
      </aside>
    </body></html>`,
    { url: PAGE, pretendToBeVisual: true, runScripts: "dangerously" },
  );
  const { window } = dom;
  const box = { x: 0, y: 0, width: 52, height: 40, top: 0, right: 52, bottom: 40, left: 0 };
  const rect = () => ({ ...box, toJSON() { return box; } });
  const list = () => [{ ...box }];
  for (const node of window.document.querySelectorAll("aside, aside a")) {
    node.getBoundingClientRect = rect;
    node.getClientRects = list;
  }
  window.HTMLElement.prototype.getBoundingClientRect = rect;
  window.HTMLElement.prototype.getClientRects = list;
  window.ResizeObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  };
  const fetches = [];
  window.fetch = async (path) => {
    fetches.push(String(path));
    throw new Error("session must not run on flyout open: " + path);
  };
  window.__nanexusHistorical = { mount(target) { target.textContent = "historical-mounted"; return () => { target.textContent = ""; }; } };
  window.__nanexusLive = { mount(target) { target.id = "live-mounted"; return () => { target.removeAttribute("id"); }; } };
  const tag = window.document.createElement("script");
  tag.textContent = script.toString("utf8");
  window.document.body.appendChild(tag);
  return { window, fetches };
}

test("product navigation.js is the accepted flyout, not the landing page", () => {
  const text = script.toString("utf8");
  assert.notEqual(text.length, 0);
  assert.notEqual(createHash("sha256").update(script).digest("hex"), LANDING);
  assert.match(text, /nanexus-flyout/);
  assert.match(text, /__nanexusLive/);
  assert.match(text, /__nanexusHistorical/);
  assert.doesNotMatch(text, /Back to Nanexus/);
  assert.doesNotMatch(text, /Confirming Frigate session/);
});

test("N icon opens the two-button flyout without a landing page", () => {
  const { window, fetches } = load();
  const entry = window.document.getElementById("nanexus-dashboard-open");
  const flyout = window.document.getElementById("nanexus-flyout");
  const pane = window.document.getElementById("nanexus-dashboard");
  assert.ok(entry && flyout && pane);
  assert.equal(flyout.hidden, true);
  assert.equal(pane.hidden, true);
  entry.click();
  assert.equal(flyout.hidden, false);
  assert.equal(pane.hidden, true);
  assert.equal(entry.getAttribute("aria-expanded"), "true");
  const items = [...flyout.querySelectorAll("[role=menuitem]")].map((n) => n.textContent);
  assert.deepEqual(items, ["Live Monitor", "Historical Verification"]);
  assert.equal(window.document.body.textContent.includes("Back to Nanexus"), false);
  assert.equal(fetches.length, 0);
  entry.click();
  assert.equal(flyout.hidden, true);
});

test("flyout destinations mount Historical and Live without remounting the active one", () => {
  const { window } = load();
  let historical = 0;
  let live = 0;
  window.__nanexusHistorical = { mount(target) { historical += 1; target.textContent = "historical-mounted"; return () => { historical -= 1; }; } };
  window.__nanexusLive = { mount(target) { live += 1; target.textContent = "live-mounted"; return () => { live -= 1; }; } };
  const entry = window.document.getElementById("nanexus-dashboard-open");
  const flyout = window.document.getElementById("nanexus-flyout");
  const pane = window.document.getElementById("nanexus-dashboard");
  entry.click();
  flyout.querySelector("[role=menuitem]").click();
  assert.equal(flyout.hidden, true);
  assert.equal(pane.hidden, false);
  assert.equal(pane.classList.contains("nx-live"), true);
  assert.equal(live, 1);
  assert.equal(historical, 0);
  entry.click();
  flyout.querySelectorAll("[role=menuitem]")[0].click();
  assert.equal(live, 1);
  entry.click();
  flyout.querySelectorAll("[role=menuitem]")[1].click();
  assert.equal(pane.classList.contains("nx-live"), false);
  assert.equal(historical, 1);
  assert.equal(window.document.getElementById("nanexus-dashboard").textContent.includes("historical-mounted"), true);
});
