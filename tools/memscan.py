#!/usr/bin/env python3
"""Scan Cursor processes' /proc/<pid>/mem for markers. Read-only. Run as root.
Finds Cursor's processes automatically. Defaults to reactive-storage field
names; pass your own strings as args to search for anything, e.g.
sudo memscan.py mySettingName someString"""
import os, re, sys

MARKERS = [b'useOpenAIKey', b'openAIBaseUrl', b'cursor.sh']

def _ppid(pid):
    try:
        for line in open(f'/proc/{pid}/status'):
            if line.startswith('PPid:'):
                return int(line.split()[1])
    except Exception:
        pass
    return 0

def _cmd(pid):
    try:
        return open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\0', b' ').decode('utf-8', 'replace')
    except Exception:
        return ''

def cursor_main_pid():
    for p in os.listdir('/proc'):
        if not p.isdigit():
            continue
        c = _cmd(p)
        if '/usr/share/cursor/cursor' in c and '--type=' not in c:
            return p
    return None

def cursor_descendant_pids():
    main = cursor_main_pid()
    if not main:
        return []
    main = int(main)
    out = []
    for p in os.listdir('/proc'):
        if not p.isdigit() or int(p) == main:
            continue
        cur, depth = int(p), 0
        is_child = False
        while cur and depth < 25:
            if cur == main:
                is_child = True
                break
            par = _ppid(cur)
            if par == cur or par == 0:
                break
            cur = par
            depth += 1
        if is_child:
            out.append((p, _cmd(p)))
    return out

def readable_anon_ranges(pid):
    ranges = []
    try:
        maps = open(f'/proc/{pid}/maps').read()
    except Exception:
        return ranges
    for line in maps.splitlines():
        m = re.match(r'([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+ *(.*)', line)
        if not m:
            continue
        s, e, perms, path = m.groups()
        if 'r' not in perms:
            continue
        # keep anonymous / [heap] / [anon:...] only (skip file-backed)
        if path and '[' not in path:
            continue
        ranges.append((int(s, 16), int(e, 16)))
    return ranges

def scan_brief(pid):
    """Return (counts dict, list of useOpenAIKey (offset, ctx)) or None if unreadable."""
    ranges = readable_anon_ranges(pid)
    counts = {mk: 0 for mk in MARKERS}
    useopenai = []
    try:
        fd = os.open(f'/proc/{pid}/mem', os.O_RDONLY)
    except Exception:
        return None
    CHUNK = 8 * 1024 * 1024
    try:
        for s, e in ranges:
            off = s
            while off < e:
                n = min(CHUNK, e - off)
                try:
                    os.lseek(fd, off, 0)
                    data = os.read(fd, n)
                except Exception:
                    off += n
                    continue
                if not data:
                    break
                for mk in MARKERS:
                    start = 0
                    while True:
                        i = data.find(mk, start)
                        if i < 0:
                            break
                        counts[mk] += 1
                        if mk == MARKERS[0]:
                            ctx = data[max(0, i - 20):i + 70]
                            printable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in ctx)
                            useopenai.append((hex(off + i), printable))
                        start = i + 1
                off += n
    finally:
        os.close(fd)
    return counts, useopenai

def main():
    global MARKERS
    if len(sys.argv) > 1:
        MARKERS = [a.encode() for a in sys.argv[1:]]
    pids = cursor_descendant_pids()
    if not pids:
        print("no cursor descendants found")
        return
    print(f"found {len(pids)} cursor descendant process(es); scanning anon memory for {len(MARKERS)} marker(s)")
    # only detail pids that have the useOpenAIKey marker
    for pid, cmd in pids:
        sys.stdout.write(f"pid {pid} ... ")
        sys.stdout.flush()
        detail = scan_brief(pid)
        if detail is None:
            print("(unreadable)")
        else:
            mkcount, useopenai_hits = detail
            tag = ' '.join(f"{k.decode()}={v}" for k, v in mkcount.items())
            print(tag)
            if useopenai_hits:
                for pos, ctx in useopenai_hits[:3]:
                    print(f"      @{pos}  {ctx}")

main()
