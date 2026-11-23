// stories-data.js — the Why and Who tabs (hand-written). Strict JSON after the '=': no comments,
// no trailing commas, double quotes. The generator checks every reference: python3 docs/usage-map/check_stories.py
window.USAGE_MAP_STORIES = {
 "why": {
  "lead": "Balancewheel helps one developer use several AI models together. Each model does the work it is good at, at a cost the developer can see. The developer, not a vendor, decides which model reviews what, from logged results.",
  "truthNote": "Balancewheel is per developer. Each developer's config, log and scoreboard stay in that developer's home directory, and bw never uploads them. What does leave the machine is shown in the stories: seats send the brief and code to their vendors, and triage sends a question to the decision model.",
  "cards": [
   {
    "id": "together",
    "icon": "⚖️",
    "title": "Use models together, by strength and cost",
    "text": "One lead agent writes the code. Models from other vendors review it as read-only seats. Each seat has the duties its results earned, so a strong but expensive model is used only where it pays off.",
    "stories": [
     "theo-panel",
     "theo-scoreboard"
    ]
   },
   {
    "id": "blind",
    "icon": "🙈",
    "title": "Second opinions that are really independent",
    "text": "Seats review blind and in parallel, from one shared brief. Only disputed claims are cross-examined. The lead checks every finding against the code, so a finding is true because the code says so, not because a model says so.",
    "stories": [
     "theo-panel"
    ]
   },
   {
    "id": "continuity",
    "icon": "🔁",
    "title": "Work continues when a plan runs out",
    "text": "When one subscription hits its cap, bw writes a handoff and starts the next lead in the same worktree. The preferred lead comes back when its plan resets. Questions only you can answer wait in your inbox.",
    "stories": [
     "theo-cap",
     "theo-ask",
     "theo-daily"
    ]
   },
   {
    "id": "numbers",
    "icon": "📊",
    "title": "Seat choices backed by numbers",
    "text": "Every panel is logged. The scoreboard shows which seat finds real problems. A seat change must cite those numbers, so the choice follows evidence, not habit or hype.",
    "stories": [
     "theo-scoreboard",
     "theo-improve",
     "theo-guard"
    ]
   },
   {
    "id": "esc",
    "icon": "🔗",
    "title": "Works with OpenEscapement",
    "text": "Balancewheel finds out which seat policy works. OpenEscapement ships that policy to every repo in the org as a signed rule pack, so all teams use the same model guidance.",
    "stories": [
     "theo-to-esc"
    ],
    "href": "https://tensorgroup.github.io/openescapement/usage-map/#who",
    "linkLabel": "Open the OpenEscapement map →"
   }
  ]
 },
 "org": {
  "name": "Windup Workshop",
  "blurb": "Windup Workshop is a fictional software company with four teams. The same people appear in the OpenEscapement map. Here, Theo and Ana use Balancewheel on their own machines.",
  "teams": [
   {
    "id": "platform",
    "name": "Platform",
    "about": "Owns the org's rule pack and the policy portal."
   },
   {
    "id": "payments",
    "name": "Payments",
    "about": "Builds the payments service."
   },
   {
    "id": "mobile",
    "name": "Mobile",
    "about": "Builds the mobile app."
   },
   {
    "id": "leadership",
    "name": "Leadership",
    "about": "Looks across every team."
   }
  ],
  "people": [
   {
    "id": "mira",
    "name": "Mira",
    "team": "platform",
    "role": "Platform admin",
    "goal": "Publish policy changes safely and see the whole fleet.",
    "personas": [],
    "stories": [],
    "elsewhere": {
     "label": "Mira uses OpenEscapement →",
     "href": "https://tensorgroup.github.io/openescapement/usage-map/#who"
    }
   },
   {
    "id": "raj",
    "name": "Raj",
    "team": "platform",
    "role": "Pack author",
    "goal": "Release a signed pack that every repo can pin.",
    "personas": [],
    "stories": [],
    "elsewhere": {
     "label": "Raj's stories are in OpenEscapement →",
     "href": "https://tensorgroup.github.io/openescapement/usage-map/#who"
    }
   },
   {
    "id": "theo",
    "name": "Theo",
    "team": "payments",
    "role": "Developer, runs Balancewheel",
    "goal": "Ship with second opinions from other model families, and keep working when a plan runs out.",
    "personas": [
     "you",
     "lead",
     "nextlead"
    ],
    "stories": [
     "theo-daily",
     "theo-panel",
     "theo-cap",
     "theo-ask",
     "theo-scoreboard",
     "theo-guard",
     "theo-improve",
     "theo-to-esc"
    ]
   },
   {
    "id": "ana",
    "name": "Ana",
    "team": "payments",
    "role": "New developer",
    "goal": "Set up the same panel as Theo on her own machine, safely.",
    "personas": [
     "you"
    ],
    "stories": [
     "ana-setup"
    ]
   },
   {
    "id": "kai",
    "name": "Kai",
    "team": "mobile",
    "role": "Developer",
    "goal": "Use the same model guidance as Payments, without extra work.",
    "personas": [],
    "stories": [],
    "elsewhere": {
     "label": "Kai uses OpenEscapement →",
     "href": "https://tensorgroup.github.io/openescapement/usage-map/#who"
    }
   },
   {
    "id": "lena",
    "name": "Lena",
    "team": "leadership",
    "role": "Org observer",
    "goal": "Know what every team uses and what it costs.",
    "personas": [],
    "stories": [],
    "elsewhere": {
     "label": "Lena's view is in OpenEscapement →",
     "href": "https://tensorgroup.github.io/openescapement/usage-map/#who"
    }
   }
  ]
 },
 "places": [
  {
   "id": "theo-laptop",
   "title": "Theo's terminal",
   "kind": "machine",
   "owner": "theo",
   "node": "you"
  },
  {
   "id": "theo-home",
   "title": "Theo's home directory",
   "kind": "home",
   "owner": "theo",
   "node": "statedir",
   "truthFor": [
    "Theo's seats, leads and plans (config.json)",
    "Theo's panel results (metrics.jsonl)",
    "Theo's asks, handoffs and checkpoints"
   ]
  },
  {
   "id": "payments-repo",
   "title": "Payments repo (worktree)",
   "kind": "repo",
   "owner": "payments",
   "node": "worktree",
   "truthFor": [
    "the code"
   ]
  },
  {
   "id": "lead-agent",
   "title": "Lead agent",
   "kind": "agent",
   "owner": "theo",
   "node": "lead"
  },
  {
   "id": "next-lead",
   "title": "Next lead",
   "kind": "agent",
   "owner": "theo",
   "node": "nextlead"
  },
  {
   "id": "seats",
   "title": "Review seats (read-only)",
   "kind": "agent",
   "owner": "theo",
   "node": "seats"
  },
  {
   "id": "vendor-plans",
   "title": "Vendor plans",
   "kind": "service",
   "node": "quota"
  },
  {
   "id": "decision-model",
   "title": "Decision model (OpenRouter)",
   "kind": "service",
   "node": "decider"
  },
  {
   "id": "guard-broker",
   "title": "Guard broker",
   "kind": "agent",
   "owner": "theo",
   "node": "broker"
  },
  {
   "id": "pack-repo",
   "title": "Org pack repo",
   "kind": "repo",
   "owner": "platform",
   "node": "modelseats",
   "truthFor": [
    "the org's model seat policy (model-seats pack)"
   ]
  },
  {
   "id": "mobile-repo",
   "title": "Mobile repo",
   "kind": "repo",
   "owner": "mobile",
   "node": "govrepo"
  },
  {
   "id": "ana-laptop",
   "title": "Ana's terminal",
   "kind": "machine",
   "owner": "ana",
   "node": "you"
  },
  {
   "id": "ana-home",
   "title": "Ana's home directory",
   "kind": "home",
   "owner": "ana",
   "node": "config",
   "truthFor": [
    "Ana's own seats, leads and plans"
   ]
  }
 ],
 "stories": [
  {
   "id": "ana-setup",
   "person": "ana",
   "title": "Set up a machine",
   "why": "Ana does this once. Balancewheel writes only under her home directory, so nothing in a repo changes.",
   "steps": [
    {
     "place": "ana-home",
     "label": "Back up first",
     "text": "Back up every path that setup touches, before anything writes.",
     "command": "setup/backup.sh",
     "node": "backups",
     "moves": [
      {
       "from": "ana-laptop",
       "to": "ana-home",
       "what": "a backup of each path setup touches",
       "kind": "store"
      }
     ]
    },
    {
     "place": "ana-home",
     "label": "Write her config",
     "text": "Copy the example config. Replace the rows with her own plans, leads and seats. Then validate it.",
     "command": "cp balancewheel.example.json ~/.config/balancewheel/config.json\npython3 metrics/log.py config",
     "node": "config",
     "moves": [
      {
       "from": "ana-laptop",
       "to": "ana-home",
       "what": "config.json: her plans, leads and seats",
       "kind": "store"
      }
     ],
     "truth": "ana-home"
    },
    {
     "place": "ana-home",
     "label": "Record quota",
     "text": "Set Claude Code's status line to run bw-statusline, so bw sees caps without her help. The status line runs this command.",
     "command": "bw-statusline <plan>",
     "node": "quota",
     "moves": [
      {
       "from": "vendor-plans",
       "to": "ana-home",
       "what": "usage and cap of each plan",
       "kind": "store"
      }
     ]
    },
    {
     "place": "ana-laptop",
     "label": "Attack-test containment",
     "text": "Attack-test the sandbox and the guard on each runtime that needs them. A lead runs only after a recorded pass.",
     "command": "bw sandbox verify pi\nbw guard verify pi",
     "node": "attack",
     "moves": [
      {
       "from": "ana-laptop",
       "to": "ana-home",
       "what": "a recorded pass per runtime",
       "kind": "store"
      }
     ]
    },
    {
     "place": "ana-laptop",
     "label": "Optional: question triage",
     "text": "She can let a decision model settle questions that carry a strong recommendation. The hook does nothing until her profile sets triage.mode to shadow or acting.",
     "command": "bw triage install claude",
     "node": "triagehook",
     "moves": [
      {
       "from": "ana-laptop",
       "to": "ana-home",
       "what": "the triage hook, and triage.mode in config",
       "kind": "store"
      }
     ]
    },
    {
     "place": "ana-laptop",
     "label": "Check the wiring",
     "text": "Print what her config wires together.",
     "command": "bw registry --print",
     "node": "registry",
     "moves": [
      {
       "from": "ana-home",
       "to": "ana-laptop",
       "what": "the graph of plans, leads and seats",
       "kind": "read"
      }
     ]
    }
   ]
  },
  {
   "id": "theo-daily",
   "person": "theo",
   "title": "Start the day",
   "why": "One command opens the right lead for the Payments repo.",
   "steps": [
    {
     "place": "theo-laptop",
     "label": "bw start",
     "text": "bw start updates the tools in parallel, re-verifies containment, and reads quota.",
     "command": "bw start ~/code/payments",
     "node": "bwstart",
     "moves": [
      {
       "from": "vendor-plans",
       "to": "theo-home",
       "what": "a quota snapshot per plan",
       "kind": "store"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Pick the lead",
     "text": "Rotation picks the lead from the profile's lead order, quota and caps.",
     "node": "rotation",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "lead order, quota and caps",
       "kind": "read"
      }
     ],
     "truth": "theo-home"
    },
    {
     "place": "theo-laptop",
     "label": "Open a pane",
     "text": "The lead opens in an Orca pane, or a Terminal tab.",
     "node": "orca"
    },
    {
     "place": "payments-repo",
     "label": "Hold the locks",
     "text": "bw lead holds the worktree lock and the runtime lock for the session.",
     "command": "bw",
     "node": "runner",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "worktree and runtime locks",
       "kind": "store"
      }
     ]
    },
    {
     "place": "lead-agent",
     "label": "Work",
     "text": "Theo works in the lead. The lead keeps a checkpoint current at every task boundary.",
     "node": "lead",
     "moves": [
      {
       "from": "lead-agent",
       "to": "payments-repo",
       "what": "code changes",
       "kind": "store"
      },
      {
       "from": "lead-agent",
       "to": "theo-home",
       "what": "checkpoint",
       "kind": "store"
      }
     ],
     "truth": "payments-repo"
    },
    {
     "place": "theo-laptop",
     "label": "See open asks",
     "text": "bw prints a line when the worktree has open questions.",
     "command": "bw inbox --project",
     "node": "inbox",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "open asks",
       "kind": "read"
      }
     ]
    }
   ]
  },
  {
   "id": "theo-panel",
   "person": "theo",
   "title": "Review at a checkpoint",
   "why": "Several model families review the same change. Their findings count only after the lead checks them against the code.",
   "steps": [
    {
     "place": "theo-home",
     "label": "Pick the seats",
     "text": "The profile names the seats for this checkpoint, which are required, and the minimum count.",
     "node": "profile:high-stakes",
     "moves": [
      {
       "from": "theo-home",
       "to": "lead-agent",
       "what": "the seat roster for this checkpoint",
       "kind": "read"
      }
     ],
     "truth": "theo-home"
    },
    {
     "place": "lead-agent",
     "label": "One brief",
     "text": "The lead writes one shared brief.",
     "node": "p-brief",
     "moves": [
      {
       "from": "lead-agent",
       "to": "seats",
       "what": "the same brief to every seat",
       "kind": "share"
      }
     ]
    },
    {
     "place": "seats",
     "label": "Blind review",
     "text": "Seats review blind and in parallel. No seat sees another seat's findings.",
     "node": "p-blind",
     "moves": [
      {
       "from": "payments-repo",
       "to": "seats",
       "what": "the code, read-only",
       "kind": "read"
      }
     ]
    },
    {
     "place": "seats",
     "label": "Other vendors",
     "text": "Each seat is another vendor's CLI, enforced read-only. A capped seat's fallback stands in.",
     "node": "seats",
     "moves": [
      {
       "from": "seats",
       "to": "vendor-plans",
       "what": "the brief and the code each seat reads",
       "kind": "share"
      },
      {
       "from": "seats",
       "to": "lead-agent",
       "what": "findings",
       "kind": "share"
      }
     ]
    },
    {
     "place": "lead-agent",
     "label": "Cross-examine",
     "text": "Only disputed claims are cross-examined. The lead relays the claim alone, never the whole transcript.",
     "node": "p-dispute",
     "moves": [
      {
       "from": "lead-agent",
       "to": "seats",
       "what": "one disputed claim",
       "kind": "share"
      }
     ]
    },
    {
     "place": "lead-agent",
     "label": "Substance bar",
     "text": "Challenges below the substance bar are dropped.",
     "node": "p-bar"
    },
    {
     "place": "lead-agent",
     "label": "Verify",
     "text": "The lead checks every surviving finding against the code.",
     "node": "p-verify",
     "moves": [
      {
       "from": "payments-repo",
       "to": "lead-agent",
       "what": "the code",
       "kind": "read"
      }
     ],
     "truth": "payments-repo"
    },
    {
     "place": "theo-home",
     "label": "Log it",
     "text": "One panel record is appended to the log.",
     "command": "python3 metrics/log.py panel …",
     "node": "p-log",
     "moves": [
      {
       "from": "lead-agent",
       "to": "theo-home",
       "what": "panel record (metrics.jsonl)",
       "kind": "store"
      }
     ],
     "truth": "theo-home"
    }
   ]
  },
  {
   "id": "theo-cap",
   "person": "theo",
   "title": "A plan runs out",
   "why": "Work continues on another subscription, and returns when the preferred plan resets.",
   "steps": [
    {
     "place": "vendor-plans",
     "label": "Cap seen",
     "text": "The status line, a probe, Orca or bw cap records that the plan is capped.",
     "command": "bw cap <plan> --resets-at <epoch>",
     "node": "quota",
     "moves": [
      {
       "from": "vendor-plans",
       "to": "theo-home",
       "what": "cap and reset time",
       "kind": "store"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Choose the next lead",
     "text": "Rotation picks the next eligible lead. It never switches mid-turn for a plan that is only low.",
     "node": "rotation",
     "truth": "theo-home"
    },
    {
     "place": "lead-agent",
     "label": "Stop the lead",
     "text": "bw stops the lead's whole process group and confirms it is gone.",
     "node": "runner"
    },
    {
     "place": "theo-home",
     "label": "Write a handoff",
     "text": "bw writes a handoff from git facts and the lead's last checkpoint.",
     "node": "handoff",
     "moves": [
      {
       "from": "payments-repo",
       "to": "theo-home",
       "what": "git facts: branch, diff stat",
       "kind": "read"
      },
      {
       "from": "theo-home",
       "to": "theo-home",
       "what": "handoff",
       "kind": "store"
      }
     ]
    },
    {
     "place": "next-lead",
     "label": "Continue",
     "text": "The next lead starts with the handoff and continues its next: line.",
     "node": "nextlead",
     "moves": [
      {
       "from": "theo-home",
       "to": "next-lead",
       "what": "the handoff",
       "kind": "read"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Log the switch",
     "text": "bw logs the switch as session and lead events.",
     "node": "metricslog",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "session and lead events",
       "kind": "store"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Return",
     "text": "After the preferred plan resets, the next bw lead goes back to it.",
     "node": "rotation"
    }
   ]
  },
  {
   "id": "theo-ask",
   "person": "theo",
   "title": "A lead needs a decision",
   "why": "Questions reach Theo as files, and a decision model may settle the easy ones.",
   "steps": [
    {
     "place": "lead-agent",
     "label": "Decide or ask",
     "text": "The lead decides what it can. A question that is really Theo's ends with a Recommendation: line.",
     "node": "lead"
    },
    {
     "place": "theo-home",
     "label": "Ask file",
     "text": "The lead writes the question to $BW_ASKS/<id>.ask, one question per file.",
     "node": "askfile",
     "moves": [
      {
       "from": "lead-agent",
       "to": "theo-home",
       "what": "the question",
       "kind": "store"
      }
     ]
    },
    {
     "place": "decision-model",
     "label": "Triage (optional)",
     "text": "With triage on, the decision model scores the question. In acting mode a strong recommendation stands as an @bw decision.",
     "node": "decider",
     "moves": [
      {
       "from": "theo-home",
       "to": "decision-model",
       "what": "the question text",
       "kind": "share"
      },
      {
       "from": "decision-model",
       "to": "theo-home",
       "what": "a score, or an @bw decision",
       "kind": "store"
      }
     ]
    },
    {
     "place": "theo-laptop",
     "label": "Answer",
     "text": "Otherwise Theo sees the question in the inbox and answers it.",
     "command": "bw inbox\nbw answer <id> \"<text>\"",
     "node": "inbox",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "open questions",
       "kind": "read"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Deliver",
     "text": "bw writes the answer to the lead's BW_INBOX. Theo's later answer always supersedes a decision.",
     "node": "answers",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "the answer",
       "kind": "store"
      }
     ],
     "truth": "theo-home"
    },
    {
     "place": "lead-agent",
     "label": "Continue",
     "text": "The lead re-reads BW_INBOX and continues.",
     "node": "lead",
     "moves": [
      {
       "from": "theo-home",
       "to": "lead-agent",
       "what": "the answer",
       "kind": "read"
      }
     ]
    }
   ]
  },
  {
   "id": "theo-scoreboard",
   "person": "theo",
   "title": "Change seat policy from numbers",
   "why": "The log becomes a scoreboard. The scoreboard is the only reason to change a seat. It is Theo's own log, on his own machine.",
   "steps": [
    {
     "place": "theo-home",
     "label": "The log grows",
     "text": "Panels, sessions, ratings and usage accumulate in metrics.jsonl.",
     "node": "metricslog",
     "truth": "theo-home"
    },
    {
     "place": "theo-laptop",
     "label": "Dashboard",
     "text": "Render the dashboard: each seat's win share and confirmed-finding rate.",
     "command": "python3 metrics/dashboard.py",
     "node": "dashboard",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "per-seat results",
       "kind": "read"
      }
     ]
    },
    {
     "place": "theo-laptop",
     "label": "Watches",
     "text": "Watches flag panel bloat, skimmed verification, and a seat that stopped contributing.",
     "command": "python3 metrics/log.py watches",
     "node": "watches",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "watch suggestions",
       "kind": "read"
      }
     ]
    },
    {
     "place": "theo-laptop",
     "label": "Cockpit",
     "text": "The cockpit shows models, usage, history and the live graph. It serves only this machine and only reads.",
     "command": "bw cockpit",
     "node": "cockpit",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "models, usage and history",
       "kind": "read"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Change the seat",
     "text": "Theo changes the seat's roles in config. He cites the numbers, and adds a re-promotion criterion if he demotes it.",
     "node": "config",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "new seat roles, with the numbers",
       "kind": "store"
      }
     ],
     "truth": "theo-home"
    }
   ]
  },
  {
   "id": "theo-guard",
   "person": "theo",
   "title": "Audit the guard",
   "why": "Today the guard only logs. Theo's verdicts are the evidence for ever letting it act.",
   "steps": [
    {
     "place": "guard-broker",
     "label": "Judge each call",
     "text": "For a guarded lead, the broker judges each tool call and logs what it would have done.",
     "node": "broker",
     "moves": [
      {
       "from": "lead-agent",
       "to": "guard-broker",
       "what": "each tool call",
       "kind": "share"
      }
     ]
    },
    {
     "place": "theo-home",
     "label": "Log the decision",
     "text": "Decisions go to guard.jsonl, scrubbed.",
     "node": "guardlog",
     "moves": [
      {
       "from": "guard-broker",
       "to": "theo-home",
       "what": "decision (scrubbed)",
       "kind": "store"
      }
     ],
     "truth": "theo-home"
    },
    {
     "place": "theo-laptop",
     "label": "Review",
     "text": "Theo reviews the stops and a sample of the allows, and marks each right or wrong.",
     "command": "bw guard review",
     "node": "greview",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "stops and a sample of allows",
       "kind": "read"
      },
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "right or wrong verdicts",
       "kind": "store"
      }
     ]
    },
    {
     "place": "guard-broker",
     "label": "Acting mode",
     "text": "Acting mode (block, not only log) is planned. The review evidence is what would justify it.",
     "node": "guardacting",
     "status": "planned"
    }
   ]
  },
  {
   "id": "theo-improve",
   "person": "theo",
   "title": "Make it easier with use",
   "why": "bw watches how Theo works and proposes changes worth making.",
   "steps": [
    {
     "place": "theo-home",
     "label": "Evidence",
     "text": "Sessions, ratings, asks and switches are already in the log.",
     "node": "metricslog",
     "truth": "theo-home"
    },
    {
     "place": "theo-laptop",
     "label": "Suggestions",
     "text": "See what bw suggests.",
     "command": "bw improve",
     "node": "improve",
     "moves": [
      {
       "from": "theo-home",
       "to": "theo-laptop",
       "what": "suggestions from the log",
       "kind": "read"
      }
     ]
    },
    {
     "place": "theo-laptop",
     "label": "Add a note",
     "text": "Theo adds his own note on what got in the way.",
     "command": "bw improve note \"<text>\"",
     "node": "improve",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "a note",
       "kind": "store"
      }
     ]
    },
    {
     "place": "theo-laptop",
     "label": "Mark it done",
     "text": "Theo marks a suggestion dealt with. It returns only on new evidence.",
     "command": "bw improve done <key>",
     "node": "improve",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "theo-home",
       "what": "done mark",
       "kind": "store"
      }
     ]
    }
   ]
  },
  {
   "id": "theo-to-esc",
   "person": "theo",
   "title": "Ship seat policy to every team",
   "why": "Balancewheel justifies the policy. OpenEscapement distributes it. This is the only path from one developer's results to the whole org.",
   "steps": [
    {
     "place": "theo-home",
     "label": "Evidence",
     "text": "Theo's scoreboard is the evidence for each seat's duties.",
     "node": "dashboard",
     "truth": "theo-home"
    },
    {
     "place": "pack-repo",
     "label": "Write the pack",
     "text": "Today the model-seats pack is written by hand from the seat policy. Raj, the pack author, owns it.",
     "node": "modelseats",
     "moves": [
      {
       "from": "theo-laptop",
       "to": "pack-repo",
       "what": "seat policy, written by hand",
       "kind": "share"
      }
     ],
     "truth": "pack-repo"
    },
    {
     "place": "pack-repo",
     "label": "Generate it",
     "text": "Planned: packs/ regenerates the pack's catalog and changelog from the log.",
     "node": "packsexport",
     "moves": [
      {
       "from": "theo-home",
       "to": "pack-repo",
       "what": "catalog and changelog from the log",
       "kind": "share"
      }
     ],
     "status": "planned"
    },
    {
     "place": "pack-repo",
     "label": "Release",
     "text": "Raj signs a release. Each governed repo then runs esc sync, which fetches the pack and verifies the signature.",
     "command": "esc sync",
     "node": "escbin",
     "moves": [
      {
       "from": "pack-repo",
       "to": "payments-repo",
       "what": "signed rule pack, fetched by esc sync",
       "kind": "sign"
      },
      {
       "from": "pack-repo",
       "to": "mobile-repo",
       "what": "signed rule pack, fetched by esc sync",
       "kind": "sign"
      }
     ],
     "truth": "pack-repo"
    },
    {
     "place": "payments-repo",
     "label": "Agents follow it",
     "text": "Agents in a governed repo read the managed block every session. There, the pack's rule wins.",
     "node": "govrepo",
     "moves": [
      {
       "from": "payments-repo",
       "to": "lead-agent",
       "what": "the managed block",
       "kind": "read"
      }
     ]
    }
   ]
  }
 ]
};
