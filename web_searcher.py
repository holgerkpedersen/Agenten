"""Web search interface using DuckDuckGo."""

from ddg_search import search_ddg


class WebSearcher:
    @staticmethod
    def search(query, num_results=3):
        return search_ddg(query, max_results=num_results)
