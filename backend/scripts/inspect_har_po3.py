import json

from _har_path import resolve_har_path

har = json.loads(resolve_har_path().read_text(encoding="utf-8"))

for entry in har["log"]["entries"]:
    url = entry["request"]["url"]
    method = entry["request"]["method"]
    if "wit" not in url.lower() and "field" not in url.lower():
        continue
    resp = entry.get("response", {}).get("content", {}).get("text") or ""
    post = (entry.get("request", {}).get("postData") or {}).get("text") or ""
    if "Logrocon.PO" in resp or "Logrocon.PO" in post or "10333" in resp:
        print(method, url[:180])
        if post and "Logrocon.PO" in post:
            print("POST:", post[:500])
        if "Logrocon.PO" in resp:
            i = resp.find("Logrocon.PO")
            print("RESP:", resp[max(0,i-50):i+400][:450])
        print("---")
