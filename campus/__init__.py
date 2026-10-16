"""Structured UTS data and the tools the chatbot can call on it.

- ``campus.extract``: parses the knowledge base into ``campus/data/*.json``
  (``python -m campus.extract``)
- ``campus.tools``:   staff lookup, programme finder and fee estimator, plus
  their JSON schemas for any LLM provider
"""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
PROGRAMMES_PATH = DATA_DIR / "programmes.json"
STAFF_PATH = DATA_DIR / "staff.json"
