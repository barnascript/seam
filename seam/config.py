import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://seam_owner:seam@127.0.0.1:55432/seamdb")
PORT = int(os.environ.get("PORT", "8000"))
# Where the worker reaches the supplier systems. In the demo they are simulated
# inside this same app, so it points back at itself.
SUPPLIER_BASE_URL = os.environ.get("SUPPLIER_BASE_URL", f"http://127.0.0.1:{PORT}/sim/suppliers")
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "4"))
RUN_WORKER = os.environ.get("RUN_WORKER", "1") == "1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SKETCH_MODEL = os.environ.get("SKETCH_MODEL", "claude-opus-5-5")
IMAGE_COST_CENTS = int(os.environ.get("IMAGE_COST_CENTS", "4"))
DEMO_IMAGE_BUDGET_CENTS = int(os.environ.get("DEMO_IMAGE_BUDGET_CENTS", "200"))
MAX_SKETCH_BYTES = 2_000_000
ALLOW_RLS_BYPASS = os.environ.get("SEAM_ALLOW_RLS_BYPASS") == "1"
# The static site on GitHub Pages calls the API from another origin.
CORS_ORIGINS = [o for o in os.environ.get("CORS_ORIGINS", "https://barnascript.github.io").split(",") if o]
