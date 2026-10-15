"""The claude.ai artifact twin of the local page, and the snapshot it reads.

``build`` inlines the page (same markup, CSS and script) into one artifact file; in artifact mode
the script subscribes to ``live/snapshot`` in the artifact database. ``snapshot`` writes that
document's body to disk and prints ``PUSH <path>``: the Claude session relaying the run turns each
line into one database write (the artifact cannot reach this machine).

    .venv/bin/python -m tlive.serve.artifact      # writes data/artifact/sandwich_screener.html
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import orjson

PAGE = Path(__file__).parent / "page"
ROOT = Path(__file__).resolve().parents[2]
N_FLAGS, N_SANDS, N_RISK = 120, 50, 150


def build(out: Path = ROOT / "data" / "artifact" / "sandwich_screener.html") -> Path:
    html = (PAGE / "index.html").read_text()
    head = re.search(r"<head>(.*)</head>", html, re.S).group(1)
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    head = re.sub(r'<meta charset[^>]*>\s*|<meta name="viewport"[^>]*>\s*', "", head)
    head = head.replace("<title>", '<meta name="claude:viewport-fit-sides" content="cover">\n<title>', 1)
    css = "\n".join((PAGE / m).read_text() for m in re.findall(r'<link rel="stylesheet" href="static/([^"]+)">', head))
    css += "\n.app { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }\n"
    head = re.sub(r'<link rel="stylesheet" href="static/[^"]+">\s*', "", head)
    head = head.replace("</title>", "</title>\n<style>\n" + css + "</style>", 1)
    body = re.sub(r'<script src="static/([^"]+)"></script>',
                  lambda m: "<script>\n" + (PAGE / m.group(1)).read_text() + "</script>", body)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(head.strip() + "\n" + body.strip() + "\n")
    return out


def snapshot(hub, path: Path) -> Path:
    """Write the database body for ``live/snapshot`` and announce it."""
    body = {"updated_ms": int(time.time() * 1000), "stats": hub.stats,
            "flags": list(reversed(list(hub.flags)[-N_FLAGS:])),
            "sands": list(reversed(list(hub.sandwiches)[-N_SANDS:])),
            "risk": list(reversed(list(hub.risk)[-N_RISK:])),
            "visible": list(reversed(list(hub.visible_optimal)[-N_FLAGS:])),
            "strict": list(reversed(list(hub.strict_optimal)[-N_SANDS:]))}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(orjson.dumps(body, option=orjson.OPT_SERIALIZE_NUMPY))
    tmp.rename(path)
    print(f"PUSH {path}", flush=True)
    return path


if __name__ == "__main__":
    print(build())
