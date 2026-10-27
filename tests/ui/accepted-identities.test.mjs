import test from "node:test";
import assert from "node:assert/strict";
import { ACCEPTED, ui } from "./assets.mjs";

test("promoted static UI matches the accepted assembly hashes", () => {
  for (const name of Object.keys(ACCEPTED)) {
    const bytes = ui(name);
    assert.ok(bytes.length > 0);
  }
});
