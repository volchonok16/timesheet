import json
import re
from pathlib import Path

har = json.loads(Path(r"c:\Users\avolc\Downloads\tfs.t2.ru.har").read_text(encoding="utf-8"))

for entry in har["log"]["entries"]:
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    if "allowedValues" not in resp:
        continue
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        continue
    blob = json.dumps(data, ensure_ascii=False)
    if "Logrocon" not in blob and "10333" not in blob:
        continue
    print("URL:", entry["request"]["url"][:120])
    # walk json for allowedValues near Logrocon.PO
    def walk(node, path=""):
        if isinstance(node, dict):
            ref = node.get("referenceName") or node.get("id")
            if ref == "Logrocon.PO" or node.get("name") == "Logrocon.PO":
                print("FOUND FIELD:", json.dumps(node, ensure_ascii=False)[:2000])
            if "allowedValues" in node and ("PO" in path or "Logrocon" in json.dumps(node, ensure_ascii=False)):
                av = node.get("allowedValues")
                if av and len(av) > 5:
                    print(f"allowedValues count={len(av)} path={path}")
                    for item in av[:5]:
                        print(" ", item)
                    print(" ...")
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    walk(data)
    print("---")
