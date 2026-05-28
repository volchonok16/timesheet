import json
from pathlib import Path

har = json.loads(Path(r"c:\Users\avolc\Downloads\tfs.t2.ru.har").read_text(encoding="utf-8"))

for entry in har["log"]["entries"]:
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    if "allowedValues" not in resp or "задача" not in resp.lower() and "Task" not in resp:
        if "type\":\"allowedValues\"" not in resp:
            continue
    if "allowedValues" in resp and ("Logrocon" in resp or "ERP" in resp or "10333" in resp):
        print("URL:", entry["request"]["url"][:120])
        print("len:", len(resp))
        Path("har_po_dump.txt").write_text(resp[:500000], encoding="utf-8")
        print("dumped to har_po_dump.txt")
        break

# Also find entries with large ERP lists
for entry in har["log"]["entries"]:
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    if resp.count("ERP") > 20:
        print("ERP heavy URL:", entry["request"]["url"][:120], "count", resp.count("ERP"))
        if "Logrocon.PO" in resp or "10333" in resp:
            Path("har_po_dump2.txt").write_text(resp[:800000], encoding="utf-8")
