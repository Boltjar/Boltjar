"""The core nodes: value constants, triggers, data, logic, AI, stores, outputs."""
import pathlib

from boltjar.models import load_models

from . import builtin  # noqa: F401  (importing registers every @node)

# Declarative model manifests live alongside the core nodes (one TOML per model).
load_models(pathlib.Path(__file__).resolve().parent / "models")
