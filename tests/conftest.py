import os
import pytest
from dotenv import load_dotenv

# Load real credentials from .env at the project root
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

def pytest_configure(config):
    """Register custom marks so pytest does not warn about unknown marks."""
    config.addinivalue_line(
        "markers",
        "expensive: marks tests that call the Claude API (skipped by default, run with -m expensive)"
    )
