// pi_guard.ts — pi's guard client: forward each tool_call to the broker, do what it says.
//
// Thin on purpose. Normalisation and every decision live in the broker. This file has mode
// logic only for failure: an unreachable broker, an error or malformed reply, or no reply within
// the deadline is "allow plus a marker line" in shadow and a block in acting. It is inert only
// when BW_GUARD_MODE is unset; set with the socket or token missing, it is a failure.
// Only `node:` builtins and erasable syntax, so `node --experimental-strip-types` runs it.
// bw copies this file to a dir no lead can write and passes it with `-e`.
import net from "node:net";
import fs from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DEADLINE_MS = 2000;
const MAX_REQUEST = 1024 * 1024; // a request line over this is replaced by a stub
const RUNTIME = "pi";

type Verdict = { decision: string; reason: string };

function writeMarker(path: string | undefined, mode: string, why: string): void {
  if (!path) return;
  try {
    // lstat first: never follow a symlink. A path that does not exist yet is fine.
    try {
      if (fs.lstatSync(path).isSymbolicLink()) return;
    } catch (e: any) {
      if (e?.code !== "ENOENT") return;
    }
    const fd = fs.openSync(path, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
    try {
      fs.writeSync(fd, JSON.stringify({ ts: new Date().toISOString(), runtime: RUNTIME, mode, why }) + "\n");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    // a marker that cannot be written must not change the answer
  }
}

function frame(token: string, cwd: string, payload: unknown, toolName: string): string {
  let line = JSON.stringify({ v: 1, token, runtime: RUNTIME, cwd, payload }) + "\n";
  if (Buffer.byteLength(line) > MAX_REQUEST) {
    line = JSON.stringify({ v: 1, token, runtime: RUNTIME, cwd, payload: { oversized: true, tool: toolName } }) + "\n";
  }
  return line;
}

function ask(sockPath: string, line: string): Promise<Verdict> {
  return new Promise((resolve, reject) => {
    let buf = "";
    let done = false;
    const finish = (fn: () => void) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      conn.destroy();
      fn();
    };
    const conn = net.createConnection(sockPath);
    const timer = setTimeout(() => finish(() => reject(new Error(`no reply within ${DEADLINE_MS / 1000}s`))), DEADLINE_MS);
    conn.setEncoding("utf8");
    conn.on("connect", () => conn.write(line));
    conn.on("data", (d: string) => {
      buf += d;
      if (buf.includes("\n")) finish(() => {
        try {
          const rep = JSON.parse(buf.split("\n")[0]);
          if (rep === null || typeof rep !== "object" || rep.v !== 1) throw new Error("malformed reply");
          if ("error" in rep) throw new Error(`broker error: ${String(rep.error).slice(0, 100)}`);
          if (!["allow", "ask", "block"].includes(rep.decision)) throw new Error("unknown decision");
          resolve({ decision: rep.decision, reason: typeof rep.reason === "string" ? rep.reason : "" });
        } catch (e: any) {
          reject(e instanceof SyntaxError ? new Error("malformed reply") : e);
        }
      });
    });
    conn.on("error", (e: Error) => finish(() => reject(e)));
    conn.on("close", () => finish(() => reject(new Error(buf.trim() ? "malformed reply" : "empty reply"))));
  });
}

function describe(event: any): string {
  const cmd = event?.input?.command;
  if (typeof cmd === "string") return cmd.slice(0, 500);
  return `${event?.toolName ?? "tool"} ${JSON.stringify(event?.input ?? {})}`.slice(0, 500);
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event: any, ctx: any) => {
    const mode = process.env.BW_GUARD_MODE;
    if (!mode) return undefined; // inert: no guard variables, no opinion
    try {
      const sock = process.env.BW_GUARD_SOCKET;
      const token = process.env.BW_GUARD_TOKEN;
      if (!sock) throw new Error("BW_GUARD_SOCKET is not set");
      if (!token) throw new Error("BW_GUARD_TOKEN is not set");
      if (mode !== "shadow" && mode !== "acting") throw new Error("BW_GUARD_MODE is neither shadow nor acting");
      const v = await ask(sock, frame(token, String(ctx?.cwd ?? process.cwd()), event, String(event?.toolName ?? "unknown")));
      if (v.decision === "allow") return undefined;
      if (v.decision === "block") return { block: true, reason: v.reason };
      if (ctx?.hasUI) {
        const pick = await ctx.ui.select(`${describe(event)}\n\n${v.reason}\n\nAllow?`, ["Yes", "No"]);
        return pick === "Yes" ? undefined : { block: true, reason: "declined by the user" };
      }
      return { block: true, reason: v.reason };
    } catch (e: any) {
      const why = String(e?.message ?? e);
      if (mode === "shadow") {
        writeMarker(process.env.BW_GUARD_MARKERS, mode, why);
        return undefined;
      }
      return { block: true, reason: `guard unavailable: ${why}` };
    }
  });
}
