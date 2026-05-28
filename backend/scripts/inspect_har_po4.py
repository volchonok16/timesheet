import json
import re
from pathlib import Path

text = Path(r"c:\Users\avolc\Downloads\tfs.t2.ru.har").read_text(encoding="utf-8")

# decode unicode escapes in json strings
for term in ["Проект уч", "учета затрат", "Logrocon.PO", "allowedValues"]:
    print(term, text.count(term))

# find work-item-data-provider with fields
har = json.loads(text)
for entry in har["log"]["entries"]:
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    if "work-item-data-provider" not in resp and "Logrocon.PO" not in resp:
        continue
    if "Logrocon.PO" in resp:
        print("URL:", entry["request"]["url"][:100])
        # find field values in work item data
        for m in re.finditer(r'"Logrocon\.PO"[^}]{0,300}', resp):
            print(m.group()[:300])
        # search for picklist in same response
        if "picklist" in resp.lower() or "allowed" in resp.lower():
            idx = resp.find("Logrocon.PO")
            chunk = resp[idx:idx+5000] if idx>=0 else resp[:5000]
            # find value array
            vm = re.search(r'"referenceName":"Logrocon\.PO".{0,2000}', chunk)
            if vm:
                print("FIELD META:", vm.group()[:800])

# Extract unique strings that look like cost projects from form metadata
values = set()
for m in re.finditer(r'"value":"([^"]{2,120})"', text):
    v = m.group(1)
    if v.startswith("ERP") or v.startswith("SAP") or "Support" in v or "Digital" in v:
        values.add(v)
print("\nSample values count:", len(values))
for v in sorted(values)[:60]:
    print(v)
