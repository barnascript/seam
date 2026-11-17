import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, handlers, jobs, seed, service, sim, techpack  # noqa: F401  handlers registers jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.check_role()
    db.migrate()
    seed.ensure_demo()
    worker = None
    if config.RUN_WORKER:
        worker = jobs.Worker(config.WORKER_THREADS)
        worker.start()
    yield
    if worker:
        worker.stop()
    db.close()


app = FastAPI(title="Seam", version="1.0.0", lifespan=lifespan,
              description="Sketch to supplier quote, on one record per garment.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
    max_age=600,
)
app.include_router(sim.router)


@app.exception_handler(service.NotFound)
def _nf(_, e):
    return JSONResponse({"error": str(e)}, 404)


@app.exception_handler(service.Conflict)
def _cf(_, e):
    return JSONResponse({"error": str(e)}, 409)


@app.exception_handler(service.Invalid)
def _iv(_, e):
    return JSONResponse({"error": str(e)}, 422)


def brand(x_api_key: str = Header(default="")):
    b = seed.brand_for_key(x_api_key) if x_api_key else None
    if not b:
        raise HTTPException(401, "missing or wrong X-API-Key")
    return b


# ---------- meta ----------

@app.get("/health")
def health():
    with db.tx() as conn:
        conn.execute("select 1")
    return {"ok": True}


@app.get("/api/demo-brands")
def demo_brands():
    return [{"name": b["name"], "key": b["key"]} for b in seed.DEMO_BRANDS]


@app.get("/api/catalogue")
def catalogue():
    return techpack.catalogue()


@app.get("/api/me")
def me(b=Depends(brand)):
    return {"brand": b, "budget": service.budget(b["id"]), "jobs": jobs.stats(b["id"])}


# ---------- styles ----------

@app.get("/api/styles")
def list_styles(limit: int = Query(50, ge=1, le=200), b=Depends(brand)):
    return service.list_styles(b["id"], limit)


@app.post("/api/styles", status_code=202)
def create_style(
    code: str = Form(..., min_length=1, max_length=40),
    name: str = Form(..., min_length=1, max_length=120),
    garment: str = Form(...),
    season: str | None = Form(None),
    fabric: str | None = Form(None),
    features: str = Form(""),
    sketch: UploadFile = File(...),
    b=Depends(brand),
):
    data = sketch.file.read(config.MAX_SKETCH_BYTES + 1)
    style_id = service.create_style(
        b["id"], code=code.strip(), name=name.strip(), garment=garment, season=season, fabric=fabric or None,
        features=[f for f in features.split(",") if f], content_type=sketch.content_type or "application/octet-stream",
        data=data,
    )
    return {"id": style_id, "status": "drafting"}


@app.get("/api/styles/{style_id}")
def get_style(style_id: UUID, b=Depends(brand)):
    return service.get_style(b["id"], style_id)


@app.get("/api/styles/{style_id}/sketch")
def get_sketch(style_id: UUID, b=Depends(brand)):
    ctype, data = service.sketch_of(b["id"], style_id)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/styles/{style_id}/versions/{n}")
def get_version(style_id: UUID, n: int, b=Depends(brand)):
    return service.get_version(b["id"], style_id, n)


@app.get("/api/styles/{style_id}/diff")
def diff(style_id: UUID, a: int, to: int, b=Depends(brand)):
    return service.diff_versions(b["id"], style_id, a, to)


class Edit(BaseModel):
    fabric: str | None = None
    features: list[str] | None = None
    measurements: dict[str, float] | None = Field(default=None, description="New size-M value per point of measure; other sizes follow the grade rules.")
    designer_note: str | None = Field(default=None, max_length=500)


@app.post("/api/styles/{style_id}/versions", status_code=201)
def edit(style_id: UUID, change: Edit, b=Depends(brand)):
    return service.edit(b["id"], style_id, change.model_dump(exclude_none=True))


@app.post("/api/versions/{version_id}/approve")
def approve(version_id: UUID, b=Depends(brand)):
    return service.approve(b["id"], version_id)


@app.get("/api/styles/{style_id}/timeline")
def timeline(style_id: UUID, b=Depends(brand)):
    return service.timeline(b["id"], style_id)


# ---------- suppliers ----------

@app.get("/api/suppliers")
def suppliers(b=Depends(brand)):
    return service.suppliers(b["id"])


class Send(BaseModel):
    suppliers: list[str] | None = None


@app.post("/api/versions/{version_id}/send", status_code=202)
def send(version_id: UUID, body: Send | None = None, b=Depends(brand)):
    return service.send_to_suppliers(b["id"], version_id, (body.suppliers if body else None))


# ---------- images ----------

class Shots(BaseModel):
    shots: list[str] = Field(default_factory=lambda: list(service.SHOTS))


@app.post("/api/versions/{version_id}/images", status_code=202)
def images(version_id: UUID, body: Shots | None = None, b=Depends(brand)):
    return service.request_images(b["id"], version_id, (body or Shots()).shots)


@app.get("/api/images/{image_id}.svg")
def image(image_id: UUID, b=Depends(brand)):
    return Response(service.image_svg(b["id"], image_id), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=86400, immutable"})


# ---------- page ----------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


def main():
    import uvicorn
    uvicorn.run("seam.api:app", host="0.0.0.0", port=config.PORT, log_level=os.environ.get("LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
