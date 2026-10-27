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
  "historical.js": "b23512a112527b2180687d42b7886d983edd78e284539ff2ef8210acafecdd0d",
  "navigation.css": "997c3e01dc2ad98b56e2ac15099c647c4891f93c48efe1a7a884e457ca7e3b22",
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
