import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.demo:
        if args.host != "127.0.0.1":
            parser.error("Demo mode must bind to 127.0.0.1")
        root = Path(__file__).resolve().parents[1] / ".demo"
        os.environ["DATA_DIR"] = str(root)
        os.environ["DATABASE_PATH"] = str(root / "booking.db")
        os.environ["ADMIN_CHAT_ID"] = "1"
        os.environ["BOT_TOKEN"] = ""
    import uvicorn
    from webapp.app import create_app
    uvicorn.run(create_app(demo=args.demo), host=args.host, port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
