Review panels. bw runs read-only reviewer seats from other vendors. You judge their findings.

This profile convenes a panel at these checkpoints:
$checkpoints
$deep
- At each of those checkpoints, convene a panel. Do not ask the user first. At any other point, convene one only when the user asks.
- To convene, pick a new random id of 8 letters and digits. Write the request with your file-write tool to $request_dir/.tmp-<id>. Then run: mv $request_dir_sh/.tmp-<id> $request_dir_sh/<id>.request
- Use these literal paths. Do not use shell variables in them.
- The request is JSON: {"v": 1, "type": "panel", "nonce": "<another random word>", "checkpoint": "<checkpoint>", "target": "<what to review, one line>", "focus": "<risks to look at>", "stakes": "normal", "paths": ["<repo-relative files to start with>"]}
- bw answers in $view_root/responses/<id>.json within a few seconds: {"ok": true, "panel": "<panel id>", "nonce": ...} or {"ok": false, "error": "..."}. If the nonce is not yours, that id was used before: send the request again with a new id. On an error, do what the error says, then send the request again with a new id.
- Keep working while the panel runs. Do not act on the checkpoint's output (build the design, merge the branch) until the panel is logged.
- At each task boundary, read $view_root/<panel id>/status.json. When "state" is "ready" or "failed", follow $view_root/<panel id>/next.md.
- When you have nothing else to do, run exactly the next line, with no other words. It returns in two minutes or less. Exit status 3 means still working: run it again.
`python3 $wait_sh`
- Never set, export, or unset a variable whose name starts with BW_.
- Verify every finding against the code yourself. A seat's confidence is not evidence.
- Relay one disputed claim at a time with an ask request, to a seat that did not raise the finding. Never paste a seat's output to another seat.
- Log every panel with a log request, also a short or failed one. A panel that is not logged does not count.
- Then tell the user three lines: the seats and who was absent; the counts and the change that matters most; any dispute and how you ruled. The log response gives you the first line; if it says "follow_up": true, read next.md again first.
