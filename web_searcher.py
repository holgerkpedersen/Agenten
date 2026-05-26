from ddg_search import search_ddg


class WebSearcher:
    @staticmethod
    def search(query, num_results=3):
        return search_ddg(query, max_results=num_results)

    @staticmethod
    def evaluate_techstack(tech_name):
        robust = ["React", "Flask", "FastAPI", "PostgreSQL", "Redis", "Docker"]
        if tech_name in robust:
            return "robust"
        return "ukendt"
