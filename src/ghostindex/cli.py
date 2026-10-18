"""GhostIndex CLI.

Commands: index, update, search, list, stats, remove, vacuum, doctor.
Exit codes: 0 = success (search: matches found), 1 = search found
nothing or remove found no such root, 2 = could not run (bad root,
no index yet, FTS5 unavailable). All output token figures are
estimates from the estimator named in the index.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__, indexer, search as search_mod, store, tokens


def _db_path(args) -> Path:
    if getattr(args, "db", None):
        return Path(args.db).expanduser()
    return store.default_db_path()


def _open(args, create: bool) -> sqlite3.Connection:
    return store.open_store(_db_path(args), create=create)


def _print_update(report: indexer.UpdateReport, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.as_dict(), indent=2))
        return
    print(f"Indexed {report.root}")
    print(f"  added {report.added}, changed {report.changed}, "
          f"removed {report.removed}, unchanged {report.unchanged}")
    skipped = (report.skipped_binary_content + report.skipped_too_large
               + report.skipped_unreadable)
    if skipped:
        print(f"  skipped while reading: {skipped} "
              f"(binary content {report.skipped_binary_content}, "
              f"too large {report.skipped_too_large}, "
              f"unreadable {report.skipped_unreadable})")
    ws = report.walk_skips
    walk_skipped = sum(ws.values()) if ws else 0
    if walk_skipped:
        print(f"  skipped by ignore rules: {walk_skipped} {ws}")
    print(f"  files in index for this root: {report.files_indexed}, "
          f"~{report.total_tokens} estimated tokens")
    print(f"  estimator: {tokens.method()}")


def cmd_index(args) -> int:
    try:
        conn = _open(args, create=True)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    db = _db_path(args)
    rc = 0
    for raw in args.roots:
        root = Path(raw).expanduser()
        if not root.is_dir():
            print(f"error: not a directory: {raw}", file=sys.stderr)
            rc = 2
            continue
        report = indexer.index_root(conn, root, max_bytes=args.max_bytes,
                                    db_path=db)
        _print_update(report, args.json)
    conn.close()
    return rc


def cmd_update(args) -> int:
    try:
        conn = _open(args, create=True)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    db = _db_path(args)
    if args.roots:
        targets = [Path(r).expanduser() for r in args.roots]
    else:
        targets = [Path(r) for r in indexer.known_roots(conn)]
        if not targets:
            print("error: no indexed roots yet (run: ghostindex index <root>)",
                  file=sys.stderr)
            conn.close()
            return 2
    rc = 0
    for root in targets:
        if not root.is_dir():
            print(f"error: not a directory (was indexed, now gone?): {root}",
                  file=sys.stderr)
            rc = 2
            continue
        report = indexer.index_root(conn, root, max_bytes=args.max_bytes,
                                    db_path=db)
        _print_update(report, args.json)
    conn.close()
    return rc


def _search_defaults() -> dict:
    """The search caps in force when no flags override them, read
    from the same constants cmd_search runs with, so stats/doctor
    can never drift from what search reports in its own output."""
    return {
        "limit": search_mod.DEFAULT_LIMIT,
        "total_tokens": search_mod.DEFAULT_TOTAL_TOKENS,
        "per_hit_tokens": search_mod.DEFAULT_PER_HIT_TOKENS,
    }


def _print_hit(position: int, hit: search_mod.Hit) -> None:
    kind = "name match" if hit.name_only else "match"
    print(f"{position}. {hit.rel} ({hit.language}, {kind})")
    print(f"   root: {hit.root}")
    print(f"   excerpt ~{hit.excerpt_tokens} estimated tokens, "
          f"whole file ~{hit.file_tokens}")
    if hit.excerpt_truncated:
        print("   excerpt shortened by the per-hit token cap")
    for window in hit.windows:
        print(f"   L{window.start_line}-L{window.end_line}")
        for lineno, text, is_match in window.lines:
            marker = ">" if is_match else " "
            print(f"   {marker} {lineno:>5}  {text}")
    print()


def _cap_notice(result: search_mod.SearchResult) -> str:
    """One plain sentence naming every limit that cut a search."""
    limits = result.limits
    reasons = []
    for reason in result.capped_by:
        if reason == "limit":
            reasons.append(f"hit limit (--limit {limits.get('limit')})")
        elif reason == "total_tokens":
            reasons.append(
                f"total excerpt token budget "
                f"(--max-tokens {limits.get('total_tokens')})")
        elif reason == "per_hit_tokens":
            reasons.append("per-hit token cap "
                           "(at least one excerpt was shortened)")
    text = "note: results capped by " + "; ".join(reasons) + "."
    if len(result) < result.total_matches:
        text += (f" Showing {len(result)} of {result.total_matches} "
                 f"matching files; raise the limits to see more.")
    return text


def cmd_search(args) -> int:
    try:
        conn = _open(args, create=False)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    root_filter = None
    if args.root:
        root_filter = str(Path(args.root).expanduser().resolve())
        if root_filter not in indexer.known_roots(conn):
            print(f"error: root not in the index: {args.root}", file=sys.stderr)
            conn.close()
            return 2
    try:
        result = search_mod.search(
            conn, args.query, root=root_filter, limit=args.limit,
            context=args.context, any_terms=args.any,
            total_tokens=args.max_tokens,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 2
    conn.close()
    if args.json:
        payload = {
            "query": args.query,
            "estimator": tokens.method(),
        }
        payload.update(result.summary_dict())
        payload["hits"] = [h.as_dict() for h in result]
        print(json.dumps(payload, indent=2))
    elif result:
        print(f"{len(result)} of {result.total_matches} matching file(s) "
              f"for {args.query!r} (token figures are estimates)")
        if result.capped:
            print(_cap_notice(result))
        print()
        for i, hit in enumerate(result, 1):
            _print_hit(i, hit)
    else:
        print(f"no matches for {args.query!r}")
    return 0 if result else 1


def _root_rows(conn) -> list[dict]:
    rows = []
    for row in conn.execute(
        "SELECT p.root, p.indexed_at, p.token_method,"
        " COUNT(f.id) AS files, COALESCE(SUM(f.tokens), 0) AS tokens"
        " FROM projects p LEFT JOIN files f ON f.root = p.root"
        " GROUP BY p.root ORDER BY p.root"
    ):
        rows.append({
            "root": row["root"],
            "indexed_at": row["indexed_at"],
            "token_method": row["token_method"],
            "files": row["files"],
            "tokens": row["tokens"],
            "on_disk": Path(row["root"]).is_dir(),
        })
    return rows


def cmd_list(args) -> int:
    try:
        conn = _open(args, create=False)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rows = _root_rows(conn)
    conn.close()
    if args.json:
        print(json.dumps({"roots": rows}, indent=2))
    elif not rows:
        print("no indexed roots yet (run: ghostindex index <root>)")
    else:
        for row in rows:
            gone = "" if row["on_disk"] else "  [root no longer on disk]"
            print(f"{row['root']}{gone}")
            print(f"  {row['files']} files, ~{row['tokens']} estimated tokens, "
                  f"indexed {row['indexed_at']}")
    return 0


def cmd_stats(args) -> int:
    try:
        conn = _open(args, create=False)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    db = _db_path(args)
    reclaimable = store.freelist_bytes(conn)
    rows = _root_rows(conn)
    totals = conn.execute(
        "SELECT COUNT(*) AS files, COALESCE(SUM(tokens), 0) AS tokens,"
        " COALESCE(SUM(size), 0) AS bytes FROM files"
    ).fetchone()
    languages = [
        {"language": r["language"], "files": r["n"]}
        for r in conn.execute(
            "SELECT language, COUNT(*) AS n FROM files"
            " GROUP BY language ORDER BY n DESC")
    ]
    conn.close()
    data = {
        "db_path": str(db),
        "db_bytes": db.stat().st_size if db.is_file() else 0,
        "reclaimable_bytes": reclaimable,
        "roots": len(rows),
        "files": totals["files"],
        "total_tokens_est": totals["tokens"],
        "source_bytes": totals["bytes"],
        "estimator": tokens.method(),
        "search_defaults": _search_defaults(),
        "languages": languages,
        "per_root": rows,
    }
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"index database: {data['db_path']} ({data['db_bytes']} bytes)")
        if reclaimable:
            print(f"reclaimable by vacuum: {reclaimable} bytes "
                  f"(run: ghostindex vacuum)")
        print(f"roots: {data['roots']}, files: {data['files']}, "
              f"source bytes: {data['source_bytes']}")
        print(f"total content: ~{data['total_tokens_est']} estimated tokens")
        print(f"estimator: {data['estimator']}")
        sd = data["search_defaults"]
        print(f"search defaults: at most {sd['limit']} hits, "
              f"~{sd['total_tokens']} total excerpt tokens, "
              f"~{sd['per_hit_tokens']} per hit "
              f"(search output states when a cap binds)")
        if languages:
            print("languages: " + ", ".join(
                f"{item['language']} {item['files']}" for item in languages))
    return 0


def cmd_remove(args) -> int:
    try:
        conn = _open(args, create=False)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    root = Path(args.root).expanduser()
    if str(root.resolve()) not in indexer.known_roots(conn):
        print(f"error: root not in the index: {args.root}", file=sys.stderr)
        conn.close()
        return 1
    db = _db_path(args)
    before = db.stat().st_size if db.is_file() else 0
    removed = indexer.remove_root(conn, root)
    # Deleting rows only frees pages inside the file; vacuum so the
    # removed root's space is actually given back to the filesystem.
    store.reclaim_space(conn)
    conn.close()
    after = db.stat().st_size if db.is_file() else 0
    print(f"removed {removed} file(s) for {root.resolve()} from the index "
          f"(project files untouched)")
    print(f"index database compacted by vacuum: {before} -> {after} bytes "
          f"({before - after} reclaimed)")
    return 0


def cmd_vacuum(args) -> int:
    try:
        conn = _open(args, create=False)
    except store.StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    db = _db_path(args)
    before = db.stat().st_size if db.is_file() else 0
    store.reclaim_space(conn)
    conn.close()
    after = db.stat().st_size if db.is_file() else 0
    data = {
        "db_path": str(db),
        "bytes_before": before,
        "bytes_after": after,
        "reclaimed_bytes": before - after,
    }
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"index database: {data['db_path']}")
        print(f"vacuum: {before} -> {after} bytes "
              f"({before - after} reclaimed)")
    return 0


def cmd_doctor(args) -> int:
    db = _db_path(args)
    report: dict = {
        "db_path": str(db),
        "db_exists": db.is_file(),
        "db_bytes": db.stat().st_size if db.is_file() else 0,
        "sqlite_version": sqlite3.sqlite_version,
        "python": sys.version.split()[0],
        "estimator": tokens.method(),
        "ghostslice_estimator": tokens.using_ghostslice(),
        "search_defaults": _search_defaults(),
    }
    probe = sqlite3.connect(":memory:")
    report["fts5"] = store.fts5_available(probe)
    probe.close()
    roots: list[dict] = []
    healthy = report["fts5"]
    if db.is_file():
        try:
            conn = store.open_store(db, create=False)
            roots = _root_rows(conn)
            conn.close()
        except store.StoreError as exc:
            report["error"] = str(exc)
            healthy = False
    report["roots"] = roots
    report["missing_roots"] = [r["root"] for r in roots if not r["on_disk"]]
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"index database: {report['db_path']} "
              f"({'exists, ' + str(report['db_bytes']) + ' bytes'
                if report['db_exists'] else 'not created yet'})")
        print(f"sqlite {report['sqlite_version']}, "
              f"FTS5 {'available' if report['fts5'] else 'MISSING'}")
        print(f"estimator: {report['estimator']}")
        sd = report["search_defaults"]
        print(f"search defaults: at most {sd['limit']} hits, "
              f"~{sd['total_tokens']} total excerpt tokens, "
              f"~{sd['per_hit_tokens']} per hit "
              f"(search output states when a cap binds)")
        print(f"roots indexed: {len(roots)}")
        for row in roots:
            print(f"  {row['root']} ({row['files']} files)"
                  + ("" if row["on_disk"] else "  [no longer on disk]"))
        if "error" in report:
            print(f"error: {report['error']}")
    return 0 if healthy else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghostindex",
        description="Cross-project local search index for GhostSlice "
                    "(sqlite FTS5, offline, sliced excerpts).",
    )
    parser.add_argument("--version", action="version",
                        version=f"ghostindex {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def db_arg(p):
        p.add_argument("--db", default=None,
                       help="index database path (default: state dir, "
                            "or $GHOSTINDEX_DB)")

    p = sub.add_parser("index", help="index one or more project roots")
    db_arg(p)
    p.add_argument("--max-bytes", type=int, default=indexer.DEFAULT_MAX_BYTES,
                   help="skip files larger than this (default 2 MiB)")
    p.add_argument("--json", action="store_true")
    p.add_argument("roots", nargs="+")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("update", help="incrementally re-index by file hash")
    db_arg(p)
    p.add_argument("--max-bytes", type=int, default=indexer.DEFAULT_MAX_BYTES)
    p.add_argument("--json", action="store_true")
    p.add_argument("roots", nargs="*",
                   help="roots to update (default: all indexed roots)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("search", help="ranked search returning sliced excerpts")
    db_arg(p)
    p.add_argument("query")
    p.add_argument("--root", default=None, help="restrict to one indexed root")
    p.add_argument("--limit", type=int, default=search_mod.DEFAULT_LIMIT)
    p.add_argument("--context", type=int, default=search_mod.DEFAULT_CONTEXT,
                   help="context lines around each match line")
    p.add_argument("--any", action="store_true",
                   help="match files containing ANY term (default: ALL terms)")
    p.add_argument("--max-tokens", type=int,
                   default=search_mod.DEFAULT_TOTAL_TOKENS,
                   help="total excerpt token budget across hits")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("list", help="list indexed roots")
    db_arg(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats", help="index statistics")
    db_arg(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("remove", help="drop one root from the index")
    db_arg(p)
    p.add_argument("root")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("vacuum",
                       help="compact the index database, reclaiming "
                            "space left by removed or changed files")
    db_arg(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_vacuum)

    p = sub.add_parser("doctor", help="check sqlite/FTS5, db, and roots")
    db_arg(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
