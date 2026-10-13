"""Generate actual FastAPI schemas without configuring production dependencies."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "services/backend"))
from recorder.config import Settings
from recorder.main import create_app

target = root / "packages/contracts/openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(create_app(Settings(_env_file=None)).openapi(), indent=2, ensure_ascii=False) + "\n")
print(f"Generated {target.relative_to(root)} from validated FastAPI schemas")
