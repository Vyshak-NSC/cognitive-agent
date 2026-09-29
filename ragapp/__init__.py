"""Cognitive Persistence Agent package."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cognitive-persistence-agent")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
