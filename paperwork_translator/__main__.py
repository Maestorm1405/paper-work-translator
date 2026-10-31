import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Medical Paperwork Translator.")
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to open it from a phone on the same Wi-Fi.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Open http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port} in your browser.")
    uvicorn.run("paperwork_translator.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
