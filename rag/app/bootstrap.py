"""Production factory: root .env is loaded explicitly by uvicorn --env-file."""
import os

from .auth import AuthSettings
from .generation.agent import CounselAgent
from .generation.model import LangChainModel
from .generation.recipe import RagRuntime, RecipeShortcut
from .indexing.embedding import EmbeddingError, load_embedder
from .indexing.job import default_roots
from .indexing.service import IndexService
from .main import create_app
from .retrieval.faiss_store import FaissRetriever
from .retrieval.reranker import LLMReranker


def create_service():
    auth = AuthSettings.from_env()
    document_root, index_root = default_roots()
    agent, embedder = None, None
    try:
        embedder = load_embedder()
    except EmbeddingError:
        pass  # Index submissions record EMBEDDING_NOT_CONFIGURED; health stays 503.
    if embedder is not None and os.environ.get("LLM_API_KEY") and os.environ.get("LLM_MODEL"):
        reranker = LLMReranker.from_env() if os.environ.get('RERANK_ENABLED', 'true').lower() == 'true' else None
        retriever = FaissRetriever(index_root, embedder)
        retriever.warmup()
        recipe = RecipeShortcut(os.environ['RECIPE_STRUCTURED_FILE'], retriever) if os.environ.get('RECIPE_STRUCTURED_FILE') else None
        agent = CounselAgent(LangChainModel.from_env(), retriever, reranker=reranker,
                             item_lookup=recipe.resolve_item if recipe else None)
        if recipe:
            agent = RagRuntime(agent, recipe)
    return create_app(
        auth=auth, agent=agent,
        index_service=IndexService(document_root, index_root, embedder=embedder),
    )
