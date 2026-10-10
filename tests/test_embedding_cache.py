import sys
from types import SimpleNamespace

from salma.knowledge import embedding_model


def test_complete_model_cache_does_not_make_online_initialization_calls(monkeypatch):
    calls = []

    def load(*args, **kwargs):
        calls.append(kwargs)
        return "cached-model"

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(set_client_factory=lambda _: None)
    )
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=load)
    )
    embedding_model.cache_clear()
    try:
        assert embedding_model("ar") == "cached-model"
        assert calls == [{"device": "cpu", "local_files_only": True}]
    finally:
        embedding_model.cache_clear()


def test_missing_model_cache_allows_the_initial_download(monkeypatch):
    calls = []

    def load(*args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise OSError("Not cached")
        return "downloaded-model"

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(set_client_factory=lambda _: None)
    )
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=load)
    )
    embedding_model.cache_clear()
    try:
        assert embedding_model("ar") == "downloaded-model"
        assert calls == [{"device": "cpu", "local_files_only": True}, {"device": "cpu"}]
    finally:
        embedding_model.cache_clear()
