"""Point the app at a throwaway data folder before anything from backend is imported,
so tests never touch the real app.db, books/ or covers/."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="readerhelper-test-")
# Nothing in the tests should reach a real Ollama; a closed port makes any slip fail fast.
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
