"""GitHub API integration for Agent."""

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

    def _request(self, method, url, **kwargs):
        kwargs.setdefault('timeout', 30)
        try:
            resp = requests.request(method, url, **kwargs)
            return resp
        except requests.exceptions.RequestException as e:
            return None

    def _safe_json(self, resp):
        if resp is None:
            return {}
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return {}

    def _check_auth(self):
        if not self.token:
            return {"success": False, "error": "GITHUB_TOKEN ikke sat i .env fil"}

        resp = self._request("GET", f"{self.api}/user", headers=self.headers)
        if resp is None:
            return {"success": False, "error": "GitHub API netværksfejl"}
        if resp.status_code == 200:
            user = self._safe_json(resp)
            return {"success": True, "login": user.get("login"), "email": user.get("email")}
        return {"success": False, "error": f"GitHub auth fejlede: {resp.status_code}"}

    def create_repo(self, name, description="", private=False):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        data = {"name": name, "description": description, "private": private, "auto_init": False}
        resp = self._request("POST", f"{self.api}/user/repos", headers=self.headers, json=data)
        if resp is None:
            return {"success": False, "error": "GitHub API netværksfejl"}
        if resp.status_code == 201:
            repo = self._safe_json(resp)
            return {
                "success": True,
                "url": repo.get("html_url"),
                "clone_url": repo.get("clone_url"),
                "ssh_url": repo.get("ssh_url")
            }
        err = self._safe_json(resp).get('message', str(resp.status_code))
        return {"success": False, "error": f"GitHub API fejl: {resp.status_code} - {err}"}

    def list_repos(self):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        resp = self._request("GET", f"{self.api}/user/repos?per_page=50&sort=updated", headers=self.headers)
        if resp is None:
            return {"success": False, "error": "GitHub API netværksfejl"}
        if resp.status_code == 200:
            repos = self._safe_json(resp)
            return {"success": True, "repos": [{"name": r["name"], "url": r["html_url"], "private": r["private"]} for r in repos]}
        return {"success": False, "error": str(resp.status_code)}

    def create_issue(self, owner, repo, title, body=""):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        data = {"title": title, "body": body}
        resp = self._request("POST", f"{self.api}/repos/{owner}/{repo}/issues", headers=self.headers, json=data)
        if resp is None:
            return {"success": False, "error": "GitHub API netværksfejl"}
        if resp.status_code == 201:
            issue = self._safe_json(resp)
            return {"success": True, "url": issue.get("html_url"), "number": issue.get("number")}
        return {"success": False, "error": str(resp.status_code)}

    def create_pr(self, owner, repo, title, head, base="main"):
        auth = self._check_auth()
        if not auth["success"]:
            return auth

        data = {"title": title, "head": head, "base": base}
        resp = self._request("POST", f"{self.api}/repos/{owner}/{repo}/pulls", headers=self.headers, json=data)
        if resp is None:
            return {"success": False, "error": "GitHub API netværksfejl"}
        if resp.status_code == 201:
            pr = self._safe_json(resp)
            return {"success": True, "url": pr.get("html_url"), "number": pr.get("number")}
        err = self._safe_json(resp).get('message', str(resp.status_code))
        return {"success": False, "error": f"{resp.status_code} - {err}"}
