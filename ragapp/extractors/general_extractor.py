from pathlib import Path
from ragapp.tools.extractors import extract_text

def extract_document(path:Path): return extract_text(path)
