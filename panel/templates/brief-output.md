End your answer with one fenced block marked json: a list of findings, each
{"title": "...", "severity": "critical|major|minor|nit", "where": "file:line", "claim": "..."}.
One finding per problem. "where" names the line the claim is about.
If you found nothing significant, end with an empty list: [].
You are one of several independent reviewers. You cannot see the others. Work only from the code.
