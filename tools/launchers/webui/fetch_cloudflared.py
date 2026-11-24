"""Build-time helper: download cloudflared for the image arch, with timeouts and retries (slow or flaky links)."""
import sys
import urllib.request

url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-" + sys.argv[1]
for i in range(8):
    try:
        data = urllib.request.urlopen(url, timeout=30).read()
        if len(data) > 10_000_000:
            open("/usr/local/bin/cloudflared", "wb").write(data)
            break
    except Exception as e:
        print("retry", i, e, flush=True)
else:
    sys.exit("cloudflared download failed")
