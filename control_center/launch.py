import json
import subprocess
import sys
import time
import urllib.request

from .config import DATA, PROJECT


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    for port in range(8765, 8775):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(url + "/api/overview", timeout=1) as response:
                data = json.loads(response.read())
                if "weeks" in data and "targets" in data and data.get("workspace", {}).get("allow_preparation", True):
                    print(url)
                    return
        except Exception:
            pass
        with (DATA / "server.log").open("a") as log:
            process = subprocess.Popen([sys.executable, "-m", "control_center.server", "--port", str(port)], cwd=PROJECT, stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(url + "/api/overview", timeout=1) as response:
                    if response.status == 200:
                        (DATA / "server.json").write_text(json.dumps({"pid": process.pid, "port": port, "url": url}))
                        print(url)
                        return
            except Exception:
                time.sleep(.2)
    raise RuntimeError("Could not start the local control center; inspect control_center/data/server.log")


if __name__ == "__main__":
    main()
