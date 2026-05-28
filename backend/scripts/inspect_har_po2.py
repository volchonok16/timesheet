import json
import re
from pathlib import Path

har_path = Path(r"c:\Users\avolc\Downloads\tfs.t2.ru.har")
har = json.loads(har_path.read_text(encoding="utf-8"))

patterns = ["Logrocon.PO", "10333", "allowedValues", "picklistItems", "listItems"]

for entry in har["log"]["entries"]:
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    if "10333" not in resp and "Logrocon.PO" not in resp:
        continue
    url = entry["request"]["url"]
    if "allowedValues" in resp or "picklist" in resp.lower() or "listItems" in resp:
        print("URL:", url[:160])
        # try parse json
        try:
            data = json.loads(resp)
            text = json.dumps(data, ensure_ascii=False)
        except Exception:
            text = resp
        for m in re.finditer(r'.{0,50}Logrocon\.PO.{0,500}', text):
            snippet = m.group()
            if "value" in snippet.lower() or "allowed" in snippet.lower() or "ERP" in snippet:
                print(snippet[:600])
                print("---")

# Also search ERP values which looked like cost project options
print("\n=== ERP sample values ===")
text = har_path.read_text(encoding="utf-8")
for m in re.finditer(r'"value":"(ERP[^"]{3,80})"', text):
    print(m.group(1))
    if m.start() > 0:
        pass

seen = set()
for m in re.finditer(r'ERP[^\\",]{3,80}', text):
    v = m.group().strip()
    if v not in seen and len(v) < 80:
        seen.add(v)
for v in sorted(list(seen))[:40]:
    print(v)
