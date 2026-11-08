import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _private_knowledge_db(tmp_path_factory):
    """Tests build their own knowledge base instead of touching <shared>/state/knowledge.sqlite."""
    old = os.environ.get("TD_KNOWLEDGE_DB")
    os.environ["TD_KNOWLEDGE_DB"] = str(tmp_path_factory.mktemp("knowledge") / "knowledge.sqlite")
    yield
    if old is None:
        os.environ.pop("TD_KNOWLEDGE_DB", None)
    else:
        os.environ["TD_KNOWLEDGE_DB"] = old
