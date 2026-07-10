from fastapi import APIRouter, HTTPException
import json
from pathlib import Path
from pydantic import BaseModel

from backend.models.schemas import ErrorResponse

router = APIRouter(prefix="/storage")

class SaveJsonRequest(BaseModel):
    filename: str
    data: dict

@router.post(
    path="/save-json",
    responses={
        200: {"description": "File saved successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def save_json_file(request: SaveJsonRequest):
    """Save JSON data to the project's /outputs directory."""
    try:
        # Only a plain JSON basename is allowed. Resolve and re-check the parent
        # as defense in depth against ../ traversal and absolute paths.
        requested = Path(request.filename)
        if (
            requested.name != request.filename
            or requested.suffix.lower() != ".json"
            or requested.name in {".json", "..json"}
        ):
            raise HTTPException(
                status_code=400,
                detail="filename must be a plain .json basename",
            )
        # backend/routes/storage.py -> repository root
        project_root = Path(__file__).resolve().parents[2]
        outputs_dir = project_root / "outputs"
        outputs_dir.mkdir(exist_ok=True)
        file_path = (outputs_dir / requested.name).resolve()
        if file_path.parent != outputs_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid output path")
        
        # Save JSON data to file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(request.data, f, indent=2, ensure_ascii=False)
        
        return {
            "success": True,
            "message": f"File saved successfully to {file_path}",
            "filename": request.filename
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save JSON file")