import requests
from bs4 import BeautifulSoup

class WebSearcher:
    @staticmethod
    def search(query, num_results=3):
        try:
            url = f"https://html.duckduckgo.com/html/?q={query}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for result in soup.select(".result")[:num_results]:
                title_elem = result.select_one(".result__title")
                link_elem = result.select_one(".result__url")
                snippet_elem = result.select_one(".result__snippet")
                if title_elem and link_elem:
                    results.append({
                        "title": title_elem.get_text(strip=True),
                        "url": link_elem.get("href", ""),
                        "snippet": snippet_elem.get_text(strip=True) if snippet_elem else ""
                    })
            return results
        except Exception as e:
            return [{"error": str(e)}]

    @staticmethod
    def evaluate_techstack(tech_name):
        robust = ["React", "Flask", "FastAPI", "PostgreSQL", "Redis", "Docker"]
        if tech_name in robust:
            return "robust"
        return "ukendt"