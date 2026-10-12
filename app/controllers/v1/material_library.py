"""Material library endpoints: ingest, tags, check, remix, delete.

All endpoints accept the stock ``x-api-key`` guard. Storage of files stays in
the stock library directory; only the index/tags live in config.toml [app].
"""

from pathlib import Path
from typing import Literal

from fastapi import Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.services import material_library as library
from app.utils import utils

router = new_router(dependencies=[Depends(base.verify_token)])


class IngestRequest(BaseModel):
    filename: str


class TagsRequest(BaseModel):
    filename: str
    tags: list[str]


class MatchingRequest(BaseModel):
    video_terms: list[str]


class RemixRequest(BaseModel):
    video_terms: list[str]
    video_aspect: Literal["9:16", "16:9", "1:1"] = "9:16"


@router.post("/material_ingest", summary="Start background ingest of an uploaded material")
def ingest_material(request: Request, body: IngestRequest):
    file = (body.filename or "").strip()
    job_id = library.start_ingest(file)
    return utils.get_response(200, {"job_id": job_id})


@router.get("/material_ingest/{job_id}", summary="Poll ingest job progress")
def ingest_status(request: Request, job_id: str):
    job = library.get_ingest(job_id)
    if job is None:
        raise HttpException(
            task_id=job_id, status_code=404, message="ingest job not found or expired"
        )
    return utils.get_response(200, job)


@router.get("/material_library", summary="List library files with tags/status")
def list_library(request: Request):
    return utils.get_response(200, library.list_library())


@router.put("/material_tags", summary="Edit auto-tags of a library material")
def edit_tags(request: Request, body: TagsRequest):
    request_id = base.get_task_id(request)
    try:
        result = library.set_tags(body.filename, body.tags)
    except ValueError as exc:
        raise HttpException(
            task_id=request_id, status_code=404, message=str(exc)
        )
    return utils.get_response(200, result)


@router.delete("/material_library", summary="Remove a material (file + tags)")
def remove_material(request: Request, filename: str = Query(...)):
    library.delete_material(filename)
    return utils.get_response(200, {"removed": filename})


@router.post("/material_restore", summary="Restore a skipped/rejected material after review")
def restore_material(request: Request, body: IngestRequest):
    request_id = base.get_task_id(request)
    try:
        entry = library.restore_material(body.filename)
    except ValueError as exc:
        raise HttpException(task_id=request_id, status_code=400, message=str(exc))
    return utils.get_response(200, entry)


@router.delete("/material_purge", summary="Permanently delete a quarantined material")
def purge_material(request: Request, filename: str = Query(...)):
    library.purge_material(filename)
    return utils.get_response(200, {"purged": filename})


@router.post("/materials_check", summary="Check script terms coverage in the library")
def check_materials(request: Request, body: MatchingRequest):
    result = library.match_terms([t for t in body.video_terms if t.strip()])
    return utils.get_response(200, result)


@router.post("/materials_remix", summary="Generate cheap ffmpeg variants for missing terms")
def remix_materials(request: Request, body: RemixRequest):
    missing = [t for t in body.video_terms if t.strip()]
    result = library.remix_for_missing_terms(missing, body.video_aspect)
    return utils.get_response(200, result)


@router.get("/material_trash", summary="List quarantined materials with their verdicts")
def list_trash(request: Request):
    return utils.get_response(200, library.list_trash())


@router.get("/material_preview", summary="Stream a library material for preview")
def preview_material(request: Request, filename: str = Query(...)):
    request_id = base.get_task_id(request)
    try:
        path = library.material_file_path(filename)
    except ValueError as exc:
        raise HttpException(task_id=request_id, status_code=403, message=str(exc))
    if not Path(path).is_file():
        raise HttpException(
            task_id=request_id, status_code=404, message=f"{request_id}: file not found"
        )
    media = "video/mp4" if library._kind_of(filename) == "video" else "image/jpeg"
    return FileResponse(path, media_type=media)


@router.get("/material_thumb", summary="Thumbnail for a library material")
def material_thumbnail(request: Request, filename: str = Query(...)):
    request_id = base.get_task_id(request)
    try:
        thumb = library.material_thumb_path(filename)
    except ValueError as exc:
        raise HttpException(task_id=request_id, status_code=403, message=str(exc))
    if not thumb:
        raise HttpException(
            task_id=request_id, status_code=404, message=f"{request_id}: thumbnail unavailable"
        )
    return FileResponse(thumb, media_type="image/jpeg")
