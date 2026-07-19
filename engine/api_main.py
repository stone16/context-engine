"""Engine API process entry point."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "adapters.http.app:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
