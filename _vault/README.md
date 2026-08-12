# Enterprise Documentation Vault — Infinitee (Pulse)

Project-local Vault for the God Mode Swarm (adapted from the source system's
`/Applications/MAMP/htdocs/Enterprise_Documentation_Vault/`). This holds swarm
working artifacts; it does **not** replace the project's own `docs/`.

```
_vault/
├── 00_Master_Source_Of_Truth.md   # living SSOT (architecture, APIs, flows, rules)
├── 00_Shadow_Context/             # black-box context that survives lost chats
│   ├── Context_Restore_State.md
│   └── Micro_Decisions_Log.md
├── Tasks/                         # one evidence folder per non-trivial task
├── Assets/                        # ingested external files (tracked)
├── Reports/                       # client status reports, audit scorecards
└── Master_Development_Log.md      # running log: date · agents · task · evidence
```

Do not store secrets, credentials, `.env` values, tokens, or production data here.
Templates below are intentionally empty of history — populate as real work happens.
