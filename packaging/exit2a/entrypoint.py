"""Only the explicit synthetic web rehearsal role is executable in EXIT-2A."""
import sys

if __name__ == "__main__":
    if sys.argv[1:] != ["web"]:
        raise SystemExit("Only web rehearsal is supported; worker/migration roles are disabled")
    from rehearsal import create_app
    app = create_app()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
