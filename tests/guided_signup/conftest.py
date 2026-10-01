import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
pytest_plugins = ['test_signup']
