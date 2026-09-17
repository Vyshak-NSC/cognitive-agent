import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_all_python_sources_parse():
    for path in ROOT.rglob('*.py'):
        if any(part in {'.venv', '__pycache__'} for part in path.parts):
            continue
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))

def test_streamlit_entrypoint_exists():
    assert (ROOT / 'ragapp' / 'interfaces' / 'streamlit_app' / 'main.py').is_file()

def test_api_entrypoint_exists():
    assert (ROOT / 'api.py').is_file()
