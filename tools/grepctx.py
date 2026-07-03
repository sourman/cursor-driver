#!/usr/bin/env python3
"""
grepctx — grep with char-window context for Cursor's minified JS bundles.
Minified bundles put everything on a few enormous lines, so `grep` dumps the
whole line. This prints a fixed-size window around each match instead.

  grepctx.py <bundle> <regex> [--window N] [--max N] [--line N]
  grepctx.py <bundle> <regex>  --files-from-colon   # treat input lines as 'file:line:col' to reprint context

Defaults: window=120 chars each side, max=40 matches.

Patterns are case-insensitive by default; pass --case to disable.

Example:
  grepctx.py workbench.glass.main.js "cannot be used"
  grepctx.py workbench.glass.main.js "useOpenAIKey" --window 200
"""
import argparse
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("pattern")
    ap.add_argument("--window", type=int, default=120, help="chars of context each side")
    ap.add_argument("--max", type=int, default=40, help="max matches to print")
    ap.add_argument("--line", type=int, default=0, help="restrict to this 1-based line")
    ap.add_argument("--case", action="store_true", help="case-sensitive")
    args = ap.parse_args()

    flags = 0 if args.case else re.IGNORECASE
    rx = re.compile(args.pattern, flags)

    # Read the whole file; for a 52MB bundle this is fine in memory.
    with open(args.bundle, encoding="utf-8", errors="replace") as f:
        data = f.read()

    # Split keeping line numbers. rfind of '\n' gives line number per offset.
    # To avoid scanning the whole file when --line is set, slice first.
    if args.line:
        lines = data.split("\n")
        if args.line - 1 >= len(lines):
            print(f"line {args.line} out of range ({len(lines)} lines)", file=sys.stderr)
            sys.exit(1)
        regions = [(args.line, lines[args.line - 1])]
    else:
        regions = []
        # iterate lines lazily but keep them; 30k lines is fine.
        for i, ln in enumerate(data.split("\n"), 1):
            if rx.search(ln):
                regions.append((i, ln))

    printed = 0
    for lineno, ln in regions:
        for m in rx.finditer(ln):
            a = max(0, m.start() - args.window)
            b = min(len(ln), m.end() + args.window)
            snippet = ln[a:b]
            col = m.start()
            print(f"\n[L{lineno}:c{col}] …{snippet}…")
            printed += 1
            if printed >= args.max:
                print(f"\n(--max {args.max} reached; raise it)", file=sys.stderr)
                return
    print(f"\n{printed} match(es).", file=sys.stderr)


if __name__ == "__main__":
    main()
