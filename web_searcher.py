"""Web search interface using DuckDuckGo."""

from ddg_search import search_ddg


class WebSearcher:
    """web searcher."""
    @staticmethod
    def search(query, num_results=3):
        """search.
        
        Args:
            query:
            num_results:"""
        return search_ddg(query, max_results=num_results)
