import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const uiDir = join(here, "../../ui/nanexus");

/** Accepted three-camera assembly bytes. Replace only in a reviewed UI revision. */
export const ACCEPTED = {
  "navigation.js": "fd6ecdc738bb72fc4d6029361ac6424f7f3b3ad479fda2a24ff080a1c11451d9",
  "navigation-live.css": "c24c246f23ee999d6bad22306833a14a4b086c3ec287c43660f1c2f47b6301e2",
  "historical.js": "5c03a420c3948ec1b515ae9350cdb5faed94e23ba53c03d6db94ccfd216f21b6",
  "navigation.css": "4627d45367e28d4b0b471ebc2561260ade38541aca15fbb207d0a44c1a6151e4",
};

export function ui(name) {
  const bytes = readFileSync(join(uiDir, name));
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== ACCEPTED[name]) {
    throw new Error(name + " sha256 " + digest + " is not the accepted baseline");
  }
  return bytes;
}

const require = createRequire(import.meta.url);
export const { JSDOM } = require("jsdom");
export const PAGE = "https://frigate.invalid/";
