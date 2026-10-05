import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("control_center.app:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
