from datetime import datetime
import sys
import urllib.request


URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5050/"


def main():
    try:
        with urllib.request.urlopen(URL, timeout=15) as response:
            ok = 200 <= response.status < 400
            status = "UP" if ok else "DEGRADED"
            print(f"{datetime.now().isoformat()} {status} {URL} {response.status}")
            raise SystemExit(0 if ok else 1)
    except Exception as exc:
        print(f"{datetime.now().isoformat()} DOWN {URL} {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()


