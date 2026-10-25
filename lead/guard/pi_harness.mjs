// pi_harness.mjs — load a pi extension against a fake pi and ctx, fire one tool_call, print the result.
//
//   node --experimental-strip-types pi_harness.mjs <extension | --none> <scenario.json>
//
// scenario: {"event": {...}, "ctx": {"hasUI", "cwd"}, "ui": {"select": "Yes"}?, "run": "<shell>"?}
// Prints {"result": <handler result or null>, "ui_prompt": <text or null>, "ran": <bool or null>, "ms": <handler time>}.
// With "run", the command executes (in ctx.cwd) only when the handler did not block, so a
// sandboxed run shows whether a guarded call reaches its effect. "--none" loads no extension.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const [extArg, scenarioPath] = process.argv.slice(2);
if (!extArg || !scenarioPath) {
  console.error("usage: pi_harness.mjs <extension|--none> <scenario.json>");
  process.exit(2);
}
const sc = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
const handlers = [];
const pi = { on: (name, fn) => { if (name === "tool_call") handlers.push(fn); } };
if (extArg !== "--none") {
  const mod = await import(pathToFileURL(path.resolve(extArg)).href);
  await mod.default(pi);
}
let prompt = null;
const ctx = {
  hasUI: !!sc.ctx?.hasUI,
  cwd: sc.ctx?.cwd,
  ui: { select: async (text) => { prompt = text; return sc.ui ? sc.ui.select : undefined; } },
};
let result = null;
const t0 = Date.now();
for (const h of handlers) {
  const r = await h(sc.event, ctx);
  if (r && r.block) { result = r; break; } // the first block short-circuits later extensions
}
const ms = Date.now() - t0;
let ran = null;
if (sc.run) {
  ran = false;
  if (!result) {
    spawnSync("/bin/sh", ["-c", sc.run], { cwd: ctx.cwd, stdio: "ignore" });
    ran = true;
  }
}
process.stdout.write(JSON.stringify({ result, ui_prompt: prompt, ran, ms }) + "\n");
