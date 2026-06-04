from fastapi import APIRouter, HTTPException
import json
from pathlib import Path
import re
from pydantic import BaseModel

from backend.models.schemas import ErrorResponse

router = APIRouter(prefix="/storage")
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")

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
        # Create outputs directory if it doesn't exist
        project_root = Path(__file__).resolve().parents[2]  # Navigate to project root
        outputs_dir = project_root / "outputs"
        outputs_dir.mkdir(exist_ok=True)
        
        filename = Path(request.filename).name
        if filename != request.filename or not SAFE_FILENAME_RE.fullmatch(filename):
            raise HTTPException(status_code=400, detail="Filename must be a simple .json file name")

        # Construct file path and verify it remains under outputs/
        file_path = (outputs_dir / filename).resolve()
        if outputs_dir.resolve() not in file_path.parents:
            raise HTTPException(status_code=400, detail="Invalid output path")
        
        # Save JSON data to file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(request.data, f, indent=2, ensure_ascii=False)
        
        return {
            "success": True,
            "message": f"File saved successfully to {file_path}",
            "filename": filename
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}") 