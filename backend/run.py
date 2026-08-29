import os
import sys
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  Criminal Network Analysis System")
    print(f"  Open http://127.0.0.1:{port} in your browser\n")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)
