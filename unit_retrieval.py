import json
import math
import os
import re
import unicodedata
from functools import lru_cache

import yaml
from fastembed import TextEmbedding

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UNIT_CATALOG_PATH = os.getenv("UNIT_CATALOG_PATH", os.path.join(BASE_DIR, "units.yml"))
EMBEDDING_MODEL = os.getenv(
    "UNIT_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)


@lru_cache(maxsize=1)
def get_embedder() -> TextEmbedding:
    return TextEmbedding(model_name=EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def load_unit_documents() -> tuple:
    with open(UNIT_CATALOG_PATH, encoding="utf-8") as catalog_file:
        units = yaml.safe_load(catalog_file) or []
    documents = []
    for unit in units:
        if not isinstance(unit, dict) or not unit.get("name"):
            continue
        based_on = unit.get("based_on") or {}
        searchable = {
            "name": unit.get("name"),
            "search_name": str(unit.get("name", "")).replace("_", " "),
            "description": unit.get("description_az", ""),
            "intents": unit.get("intents_az", []),
            "examples": unit.get("examples_az", []),
            "operation": unit.get("operation"),
            "model": based_on.get("model"),
            "measure": based_on.get("measure"),
            "outputs": unit.get("outputs", []),
            "parameters": unit.get("parameters", {}),
            "ranking": unit.get("ranking"),
            "members": unit.get("members", []),
            "time_column": unit.get("time_column"),
            "granularity": unit.get("granularity"),
            "allow_approximate": unit.get("allow_approximate", False),
        }
        text = json.dumps(searchable, ensure_ascii=False, sort_keys=True)
        documents.append({"unit": unit, "text": f"{searchable['search_name']} {text}"})
    if not documents:
        raise ValueError(f"No semantic units found in {UNIT_CATALOG_PATH}")
    return tuple(documents)


@lru_cache(maxsize=1)
def embed_unit_documents() -> tuple:
    documents = load_unit_documents()
    vectors = tuple(get_embedder().embed([document["text"] for document in documents]))
    return vectors


def cosine_similarity(left, right) -> float:
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot_product = sum(float(a) * float(b) for a, b in zip(left, right))
    return dot_product / (left_norm * right_norm)


def _stem_token(token: str) -> str:
    suffixes = (
        "larından", "lərindən", "larının", "lərinin", "larına", "lərinə",
        "lardan", "lərdən", "ların", "lərin", "ından", "indən", "unun", "ünün",
        "inin", "ının", "sının", "sinin", "ları", "ləri", "lar", "lər", "dan",
        "dən", "nin", "nın", "nun", "nün", "ına", "inə", "una", "ünə",
        "ını", "ini", "unu", "ünü", "sı", "si", "su", "sü", "ı", "i", "u",
        "ü", "a", "ə",
    )
    for suffix in suffixes:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[:-len(suffix)]
    return token


def _tokens(text: str) -> set:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("_", " ")
    return {
        _stem_token(token)
        for token in re.findall(r"[\w]+", normalized, flags=re.UNICODE)
        if len(token) >= 2
    }


def retrieve_units(query: str, top_k: int = 5) -> list:
    if not query.strip():
        raise ValueError("Retrieval query must not be empty")
    documents = load_unit_documents()
    query_vector = next(get_embedder().query_embed([query]))
    document_vectors = embed_unit_documents()
    query_tokens = _tokens(query)
    ranked = [
        {
            "unit": document["unit"],
            "score": (
                cosine_similarity(query_vector, document_vector)
                + 0.3 * len(query_tokens & _tokens(document["text"])) / max(1, len(query_tokens))
                + 0.12 * len(query_tokens & _tokens(document["unit"]["name"].replace("_", " ")))
                / max(1, len(query_tokens))
            ),
            "embedding_score": cosine_similarity(query_vector, document_vector),
        }
        for document, document_vector in zip(documents, document_vectors)
    ]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[: max(1, min(top_k, len(ranked)))]