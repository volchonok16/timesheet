import json
import re
from pathlib import Path

har_path = Path(r"c:\Users\avolc\Downloads\tfs.t2.ru.har")
har = json.loads(har_path.read_text(encoding="utf-8"))

for entry in har["log"]["entries"]:
    req = entry.get("request", {})
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    post = (req.get("postData") or {}).get("text") or ""
    blob = req.get("url", "") + post + resp
    if "Logrocon.PO" not in blob and "PO" not in blob:
        continue
    if "Logrocon.PO" in blob or "Проект учета" in blob or "учета затрат" in blob:
        print("URL:", req.get("url", "")[:140])
        for pat in ["Logrocon.PO", "allowedValues", "listItems", "picklist"]:
            if pat in blob:
                print("  has:", pat)
        idx = blob.find("Logrocon.PO")
        if idx >= 0:
            print(blob[max(0, idx - 100) : idx + 1200][:1300])
        print("---")
