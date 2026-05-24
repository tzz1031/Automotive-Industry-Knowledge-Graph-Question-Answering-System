from pathlib import Path
import runpy


TARGET = Path(__file__).resolve().parent / "graphBuild" / "import_all.py"

if not TARGET.exists():
    raise FileNotFoundError(f"Neo4j importer not found: {TARGET}")

runpy.run_path(str(TARGET), run_name="__main__")
