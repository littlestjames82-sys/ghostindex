# GhostIndex

**Find which project, and which file, holds the thing you half remember, before you slice anything.**

By Ghost Developer Studio. Working name, v0.1 per `PLAN.md`. Zero-dependency Python stdlib, local only, offline, MIT pattern.

GhostSlice cuts one project's context down once you have chosen the project. GhostIndex answers the question that comes first: across every checkout on your machine, where does this term actually live? You name the project roots. GhostIndex walks them once, stores the text in a local sqlite FTS5 index, and answers later searches from that index with ranked, bounded excerpts: root, relative path, line ranges, the matching lines with a few lines of context, and token estimates for the excerpt and for the whole file, so the cost of pulling a file in full is visible before anyone pays it.

Story in the stack: Index (find) -> Slice (cut) -> Verify (check). Index never slices a bundle and never exports context packs. It points at files; Slice does the cutting.

## Quick start

From a source checkout:

```bash
PYTHONPATH=src python3 -m ghostindex index ~/code/project-a ~/code/project-b
PYTHONPATH=src python3 -m ghostindex search "quixotic login"
```

Or installed, the same commands as `ghostindex ...`.

The index database lives at `~/.local/state/ghostindex/index.db` by default. Choose the file with `--db <path>` on any command, or set `GHOSTINDEX_DB`. Project trees are never written to by indexing or searching.

## Commands

| Command | What it does |
|---|---|
| `ghostindex index <root> [<root> ...]` | Index one or more named project roots (added / changed / removed / unchanged counts printed per root) |
| `ghostindex update [<root> ...]` | Incremental re-index by size, mtime, then sha256; with no roots it updates every indexed root |
| `ghostindex search "<terms>"` | Ranked search returning sliced excerpts, never whole files |
| `ghostindex list` | Every indexed root with file counts, estimated tokens, last update time |
| `ghostindex stats` | Database path and size, totals, per-root and per-language breakdown |
| `ghostindex remove <root>` | Drop one root from the index and compact the database, so its space is actually reclaimed; nothing in the project itself is deleted |
| `ghostindex vacuum` | Compact the index database, reclaiming space left behind by removed or changed files |
| `ghostindex doctor` | Report sqlite version, FTS5 availability, database path and size, and any indexed root missing from disk |

Search options: `--root <path>` restricts to one indexed root, `--limit N` caps hits, `--context N` sets context lines around each match (default 3), `--max-tokens N` caps the total excerpt budget across hits, `--any` matches files holding any term instead of all terms (the default), and `--json` prints machine-readable hits for agents.

Caps are never silent. Every search states how many files matched in total, and when a limit cuts the result (the hit limit, the total token budget, or the per-hit cap shortening an excerpt) the output says so and names the limit that bound, in text and in `--json` (`total_matches`, `shown`, `capped`, `capped_by`, `limits`, and `excerpt_truncated` on any shortened hit). `stats` and `doctor` print the same default caps as `search_defaults`, so the numbers agree across commands.

Search exit codes: **0** means matches found, **1** means no matches, **2** means the search could not run (no index at that path, a bad root filter, or FTS5 unavailable). A broken index is never reported as a silent empty success.

Every command accepts `--db <path>` and `--json` where noted in `--help`.

## What gets indexed, and what does not

Indexed: text-shaped files under the roots you named, under a size cap (default 2 MiB, set with `--max-bytes`).

Never indexed:

- Files and folders matched by a root's `.gitignore`, or by the `ignore` list in a root's `.ghostslice.json` profile.
- The default ignore dirs GhostSlice also skips (`.git`, `node_modules`, `dist`, `build`, `__pycache__`, virtualenvs, caches, and friends).
- Binary files (by extension, the same set Slice's scanner uses: images, archives, executables, fonts, media, `.pyc`, database files, and more).
- Anything outside the roots you named. There is no default home-directory crawl, ever.

Indexing, updating, and searching write nothing into a project root. The only file created is the index database itself.

## How search results are shaped

Each hit names the project root and the relative path, the line ranges covered, and one or more excerpt windows: plus/minus context lines around the match lines, overlapping windows merged, match lines marked with `>` in text output. Each hit carries an excerpt token estimate and a whole-file token estimate from GhostSlice's calibrated estimator when a GhostSlice install or sibling checkout is reachable, with a documented fallback estimator when it is not. Token figures are **estimates** everywhere they print, labelled with the method in force. They are budgeting aids, not tokenizer truth.

The header of every search result counts the matching files before any capping (`10 of 30 matching file(s)`), and a `note:` line names any cap that bound. A hit whose excerpt was shortened by the per-hit token cap is marked as shortened, in text and as `excerpt_truncated` in `--json`.

## Honest limits

- **Keyword full-text search only.** v0.1 is sqlite FTS5 with bm25 ranking. There is no semantic search, no embeddings, no LLM reranking, and no query understanding. A file that says it differently will not be found.
- bm25 ranks text relevance only. It knows nothing about code structure, so a heavily commented mention can outrank a terse definition. Excerpts show the match lines so a human or an agent can judge.
- The `.gitignore` matcher is a pragmatic subset (patterns, negations, directory rules, globs). Edge cases can differ from git's own answer; the common cases are pinned by tests.
- Excerpts are windowed by lines, and a single very long line (a minified bundle line, a generated one-liner) is cut mid-line around the match, with the cut ends marked, so the per-hit token cap holds even then. (This was a defect in the first v0.1 build, fixed Oct 14, 2026; see FIX_REPORT.md.)
- FTS5 stores a full copy of the indexed text inside the database file. Indexing large project sets makes a real file on disk; `doctor` reports its size. Deleted content's space is reclaimed explicitly: `remove` compacts the database as part of dropping a root, `stats` shows how many bytes are reclaimable at any time, and `ghostindex vacuum` reclaims space freed by updates without dropping a root.
- No watching: the index changes when you run `index` or `update`. Change signals belong to GhostWatch.
- No network code exists in the tool. Nothing leaves the machine.

## Examples

`examples/` holds two tiny projects that share the phrase "sunflower festival", plus a captured sample session (`examples/sample_search_session.md`) showing an index run, a cross-project search, a search for an ignored file's term returning no matches, and stats.

## Layout of this repo

| Path | What it is |
|---|---|
| `PLAN.md` | The v0.1 design: scope, contract, verification bar, risks |
| `src/ghostindex/` | The core: scanner, store (sqlite FTS5), indexer, search, CLI |
| `tests/` | Black-box CLI tests, fixture projects, hard-rule proofs |
| `examples/` | Two tiny fixture projects and a captured sample session |
