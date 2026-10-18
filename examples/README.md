# GhostIndex examples

Two tiny projects and one captured session, so you can see what the
tool does before pointing it at your own checkouts.

| Folder | What it is |
|---|---|
| `project_alpha/` | A greeter module, a README, and a `scratch_notes.py` that the project's own `.gitignore` lists (so the index must skip it) |
| `project_beta/` | A ledger module and notes; it shares the phrase "sunflower festival" with alpha, so one search spans both projects |
| `sample_search_session.md` | A real captured session: index both projects, search the shared phrase, search the ignored file's term (no matches), and show index stats |

Try it yourself from the repo root:

```bash
ghostindex index examples/project_alpha examples/project_beta --db /tmp/demo-index.db
ghostindex search "sunflower festival" --db /tmp/demo-index.db
ghostindex search scratchterm --db /tmp/demo-index.db   # exit 1: never indexed
ghostindex stats --db /tmp/demo-index.db
```

Search exit codes: 0 means matches, 1 means no matches, 2 means the
search could not run (for example, no index at that database path).
