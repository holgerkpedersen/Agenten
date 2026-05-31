"""DuckDuckGo search integration for Agent."""

import re
import urllib.request
import urllib.parse
import os
import time
from typing import List, Dict, Optional
from config import get_logger
log = get_logger(__name__)

_UA_LIST = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
]
_UA = _UA_LIST[0]

_SKIP_DOMAINS = {
    'duckduckgo.com', 'google.com', 'bing.com', 'yahoo.com',
    'facebook.com', 'twitter.com', 'instagram.com',
    'amazon.com', 'amazon.co.uk', 'amazon.de',
}

MAX_RESULTS = int(os.getenv("DDG_MAX", "10"))


def _strip_tags(html: str) -> str:
    """strip tags.
    
    Args:
        html (str):
    
    Returns:
        str"""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#x27;', "'", text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _domain(url: str) -> str:
    """domain.
    
    Args:
        url (str):
    
    Returns:
        str"""
    m = re.match(r'https?://([^/]+)', url)
    return m.group(1).lstrip('www.') if m else ''


def search_ddg(query: str, max_results: int = 5) -> List[Dict]:
    """search ddg.
    
    Args:
        query (str):
        max_results (int):
    
    Returns:
        List[Dict]"""
    params = urllib.parse.urlencode({'q': query, 'kl': 'en-us'})
    url = f'https://html.duckduckgo.com/html/?{params}'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': _UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            break
        except Exception as e:
            if attempt == 2:
                log.warning("ddg search failed for '%s' after 3 attempts: %s", query, e)
                return []
            time.sleep(0.5 * (attempt + 1))

    results = []
    blocks = re.findall(
        r'<a[^>]+class=["\']result__a["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        r'.*?<a[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    for raw_url, raw_title, raw_snippet in blocks:
        if raw_url.startswith('/'):
            uddg = re.search(r'uddg=([^&]+)', raw_url)
            if uddg:
                raw_url = urllib.parse.unquote(uddg.group(1))
            else:
                continue
        title = _strip_tags(raw_title)
        snippet = _strip_tags(raw_snippet)
        domain = _domain(raw_url)
        if domain in _SKIP_DOMAINS:
            continue
        if not raw_url.startswith('http'):
            continue
        results.append({'title': title, 'url': raw_url, 'snippet': snippet})
        if len(results) >= max_results:
            break

    return results


def websearch(query: str, max_results: Optional[int] = None) -> List[Dict]:
    """websearch.
    
    Args:
        query (str):
        max_results (Optional[int]):
    
    Returns:
        List[Dict]"""
    return search_ddg(query, max_results or MAX_RESULTS)
