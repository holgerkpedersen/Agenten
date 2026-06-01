"""Web search interface using DuckDuckGo."""

from ddg_search import search_ddg


class WebSearcher:
    """web searcher."""
    @staticmethod
    def search(query: str, num_results: int = 3) -> list[dict]:
        """search.
        
        Args:
            query:
            num_results:"""
        return search_ddg(query, max_results=num_results)
