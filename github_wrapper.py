import os
import json
import requests
from dotenv import load_dotenv

class GithubAPI:
    def __init__(self):
        load_dotenv()
        self.token = os.getenv("GITHUB_TOKEN")
        self.api = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def _check_auth(self):
        if not self.token:
            return {"success": False, "error": "GITHUB_TOKEN ikke sat i .env fil"}

        resp = requests.get(f"{self.api}/user", headers=self.headers)
        if resp.status_code == 200:
            user = resp.json()
            return {"success": True, "login": user.get("login"), "email": user.get("email")}
        return {"success": False, "error": f"GitHub auth fejlede: {resp.status_code}"}

    def create_repo(self, name, description="", private=False):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        data = {"name": name, "description": description, "private": private, "auto_init": False}
        resp = requests.post(f"{self.api}/user/repos", headers=self.headers, json=data)
        if resp.status_code == 201:
            repo = resp.json()
            return {
                "success": True,
                "url": repo["html_url"],
                "clone_url": repo["clone_url"],
                "ssh_url": repo["ssh_url"]
            }
        return {"success": False, "error": f"GitHub API fejl: {resp.status_code} - {resp.json().get('message', '')}"}

    def list_repos(self):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        resp = requests.get(f"{self.api}/user/repos?per_page=50&sort=updated", headers=self.headers)
        if resp.status_code == 200:
            repos = resp.json()
            return {"success": True, "repos": [{"name": r["name"], "url": r["html_url"], "private": r["private"]} for r in repos]}
        return {"success": False, "error": str(resp.status_code)}

    def create_issue(self, owner, repo, title, body=""):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        data = {"title": title, "body": body}
        resp = requests.post(f"{self.api}/repos/{owner}/{repo}/issues", headers=self.headers, json=data)
        if resp.status_code == 201:
            issue = resp.json()
            return {"success": True, "url": issue["html_url"], "number": issue["number"]}
        return {"success": False, "error": str(resp.status_code)}

    def create_pr(self, owner, repo, title, head, base="master"):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        data = {"title": title, "head": head, "base": base}
        resp = requests.post(f"{self.api}/repos/{owner}/{repo}/pulls", headers=self.headers, json=data)
        if resp.status_code == 201:
            pr = resp.json()
            return {"success": True, "url": pr["html_url"], "number": pr["number"]}
        return {"success": False, "error": f"{resp.status_code} - {resp.json().get('message', '')}"}
