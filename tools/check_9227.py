from __future__ import annotations

import argparse
import json
import urllib.request


def read_json(url: str):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contains", default="")
    args = ap.parse_args()
    version = read_json("http://127.0.0.1:9227/json/version")
    pages = [x for x in read_json("http://127.0.0.1:9227/json/list") if x.get("type") == "page"]
    print("browser", version.get("Browser"))
    print("pages", len(pages))
    if args.contains:
        matched = [(x.get("title"), x.get("url")) for x in pages if args.contains in (x.get("url") or "")]
        print("matched", json.dumps(matched, ensure_ascii=False))
    else:
        print("urls", json.dumps([(x.get("title"), x.get("url")) for x in pages[:20]], ensure_ascii=False))


if __name__ == "__main__":
    main()
