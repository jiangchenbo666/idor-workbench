import httpx, sys
try:
    r = httpx.get("http://127.0.0.1:8001/api/bootstrap", timeout=5)
    with open("_check_result.txt", "w", encoding="utf-8") as f:
        f.write(f"HTTP {r.status_code}\n{r.text[:500]}\n")
    print("OK")
except Exception as e:
    with open("_check_result.txt", "w", encoding="utf-8") as f:
        f.write(f"FAILED: {e}\n")
    print("FAIL")