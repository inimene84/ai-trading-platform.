import json
import urllib.request

token = "yEb9aWxYFzGnEyzOEm4akQUzZwmS1e3gCpL1rD8C7n4"
payload = json.dumps({"note": "post-deploy resume"}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8001/sentry/resume",
    data=payload,
    headers={"Content-Type": "application/json", "X-Sentry-Token": token},
    method="POST",
)
print(urllib.request.urlopen(req, timeout=30).read().decode())
req2 = urllib.request.Request("http://127.0.0.1:8001/sentry/status")
print(urllib.request.urlopen(req2, timeout=10).read().decode())
