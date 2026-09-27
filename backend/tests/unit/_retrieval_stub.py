"""构造无 chunk 的 RetrievalResult，供置信度单元测试使用。"""
from app.services.rag.retriever import RetrievalResult
from app.services.rag.rewrite import RewrittenQuery


def empty_retrieval() -> RetrievalResult:
    return RetrievalResult(
        chunks=[],
        query=RewrittenQuery(original="test"),
        vector_hits=0, keyword_hits=0)
