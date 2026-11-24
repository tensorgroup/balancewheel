$failed## Panel $panel_id
$panel_lines

## Findings
1. Read each group below. Each finding line shows: id, seat, severity, where, title.
2. Open a seat's own file when you need its full answer.
$findings

## Verify
1. Verify every finding against the code yourself.
2. Mark each one confirmed, partial, refuted, or dropped.
3. A seat's confidence is not evidence.

## Disputes
1. Relay a challenge only when it disputes facts, behavior, or severity and gives concrete evidence.
2. Do not relay an objection to wording or certainty. Log it as a dispute scored against the challenger.
3. Relay one claim only. Never relay a transcript or a seat file.
4. Ask a seat that did not raise the finding.
5. Write this body to $ask_file. For a later ask, change ask-1 to ask-2 in both places. bw puts your claim in this frame: "$frame"
$ask_body
6. Run this command: $ask_mv
7. Each dispute gets at most $max_rounds $round_word of asks. After that, you rule.
8. bw writes the answer to `$view_dir/asks/<ask id>.md`. The response gives the ask id.
9. The answer is ready when `$view_dir/status.json` shows that ask as `done`. `bw-panel-wait` counts the panel as running until then.
10. A `failed` ask did not count as a round. Send it again.
11. Do not log while an ask is open. bw refuses it.
$triage
## Log
1. Write this body to $log_file. To send a second log, use log-2.
$log_body
2. Run this command: $log_mv
3. Every one of these findings needs a verdict: $ids
4. The optional keys are:
   - `groups`: `{"f3": "g1"}` moves a finding to a group.
   - `severity`: `{"f3": "minor"}` sets a new severity.
   - `added`: findings you found yourself. Each is `{"seat", "title", "severity", "verdict", "where", "group"}`.
   - `disputes`: each is `{"summary", "challenger", "winner", "reason", "proposals": {"<seat>": "<text>"}}`.
   - `notes`: one string.
5. bw answers in `$responses/<id>.json` (the id is the request file name without `.request`) with the first line of your summary. Check that the nonce is yours.
6. When the response has `"follow_up": true`, read this file again before you tell the user. bw added a re-verify step at the end.

## Tell the user
1. Line 1: bw's first summary line.
2. Line 2: the change that matters most.
3. Line 3: any dispute, and how you ruled.
