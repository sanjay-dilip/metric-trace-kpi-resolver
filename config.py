"""Central path configuration. Import-only: no I/O, no side effects."""

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent

DATA_DIR: Path = PROJECT_ROOT / "data"
DATA_RAW_DIR: Path = DATA_DIR / "raw"
DATA_PROCESSED_DIR: Path = DATA_DIR / "processed"
DATA_SAMPLE_DIR: Path = DATA_DIR / "sample"

OUTPUTS_DIR: Path = PROJECT_ROOT / "outputs"
ASSETS_DIR: Path = PROJECT_ROOT / "assets"
