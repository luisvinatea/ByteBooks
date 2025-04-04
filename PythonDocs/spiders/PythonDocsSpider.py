import scrapy
from urllib.parse import urljoin
from scrapy.http import Response
import json
import os
import hashlib
from bs4 import BeautifulSoup

class PythonDocsSpider(scrapy.Spider):
    name = "python_docs"
    allowed_domains = ["docs.python.org"]
    start_urls = ["https://docs.python.org/3/"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chapters = []
        self.visited_urls = {} 
        if os.path.exists("outputs/python_docs.jl"):  # Optional: Load existing URLs for deduplication
            with open("outputs/python_docs.jl", "r") as f:
                for line in f:
                    item = json.loads(line)
                    content_hash = hashlib.md5(item["content"].encode("utf-8")).hexdigest()
                    self.visited_urls[item["url"]] = content_hash
            self.logger.info(f"Loaded {len(self.visited_urls)} existing URLs")

    def parse(self, response: Response):
        self.logger.info(f"Parsing: {response.url}")
        
        all_links = (
            response.css("div.sphinxsidebarwrapper a[href]") + 
            response.css("table.contentstable a[href]") +
            response.css("div[role='main'] a[href]") +
            response.css("div.body a[href]") +
            response.css("nav a[href]")
        )
        
        for link in all_links:
            title = link.css("::text").get(default="Untitled").strip()
            relative_url = link.css("::attr(href)").get()
            if self._is_valid_link(relative_url):
                full_url = urljoin(response.url, relative_url)
                if full_url not in self.visited_urls:
                    priority = self.get_priority(title)
                    self.chapters.append({
                        "title": title,
                        "url": full_url,
                        "priority": priority,
                        "parent": None
                    })
                    self.visited_urls[full_url] = None
                    yield scrapy.Request(
                        url=full_url,
                        callback=self.parse_content,
                        meta={"title": title, "priority": priority}
                    )

        toc_link = response.css("a[href='contents.html']::attr(href)").get()
        if toc_link:
            full_toc_url = urljoin(response.url, toc_link)
            if full_toc_url not in self.visited_urls:
                yield scrapy.Request(full_toc_url, callback=self.parse_toc)

    def parse_toc(self, response: Response):
        self.logger.info(f"Parsing TOC: {response.url}")
        toc_items = response.css("div.toctree-wrapper li[class^='toctree-l']")
        
        for item in toc_items:
            level = int(item.re_first(r'toctree-l(\d+)') or 1)
            link = item.css("a.reference.internal::attr(href)").get()
            title = item.css("a.reference.internal::text").get(default="Untitled").strip()
            
            if link:
                full_url = urljoin(response.url, link)
                if full_url not in self.visited_urls:
                    priority = self.get_priority(title)
                    parent = self._find_parent(item)
                    self.chapters.append({
                        "title": title,
                        "url": full_url,
                        "priority": priority + (level - 1),
                        "parent": parent,
                        "level": level
                    })
                    self.visited_urls[full_url] = None
                    yield scrapy.Request(
                        url=full_url,
                        callback=self.parse_content,
                        meta={"title": title, "priority": priority, "level": level}
                    )

    def parse_content(self, response: Response):
        if not response.headers.get("Content-Type", b"").startswith(b"text"):
            self.logger.info(f"Skipping non-text response: {response.url}")
            return
        
        if response.url.endswith(('.tar.bz2', '.epub', '.pdf', '.zip', '.png', '.jpg', '.gif')):
            self.logger.info(f"Skipping binary file: {response.url}")
            return

        title = response.meta["title"]
        priority = response.meta["priority"]
        level = response.meta.get("level", 1)

        content = (
            response.css("div.document").get() or 
            response.css("div[role='main']").get() or 
            response.css("div.body").get() or 
            response.css("body").get(default="<p>No content available</p>")
        )

        soup = BeautifulSoup(content, 'html.parser')
        internal_links = {}
        for link in soup.find_all('a', class_='reference internal'):
            href = link.get('href')
            if href:
                internal_links[href] = link.get_text(strip=True) or "Untitled"

        content_str = str(soup)
        ids = [tag['id'] for tag in soup.find_all(id=True)]
        self.logger.debug(f"IDs in {response.url}: {ids[:10]}")
        content_hash = hashlib.md5(content_str.encode("utf-8")).hexdigest()
        if response.url in self.visited_urls and self.visited_urls[response.url] == content_hash:
            self.logger.debug(f"Skipping unchanged content: {response.url}")
            return

        item = {
            "title": title,
            "priority": priority,
            "url": response.url,
            "content": content_str,
            "level": level,
            "internal_links": internal_links
        }
        self.visited_urls[response.url] = content_hash
        yield item

        content_links = response.css("div[role='main'] a[href], div.body a[href], div.sphinxsidebarwrapper a[href]")
        for link in content_links:
            relative_url = link.css("::attr(href)").get()
            if self._is_valid_link(relative_url):
                full_url = urljoin(response.url, relative_url)
                if full_url not in self.visited_urls:
                    link_title = link.css("::text").get(default="Untitled").strip()
                    yield scrapy.Request(
                        url=full_url,
                        callback=self.parse_content,
                        meta={"title": link_title, "priority": priority + 1, "level": level}
                    )

    def _is_valid_link(self, url: str) -> bool:
        if not url:
            return False
        invalid_extensions = ('.pdf', '.zip', '.tar.bz2', '.epub', '.png', '.jpg', '.gif')
        invalid_prefixes = ('javascript:', 'http://', 'https://', 'mailto:')
        valid_paths = ('whatsnew/', 'howto/', 'c-api/', 'deprecations/', 'library/', 'reference/', 'faq/')
        return (not any(url.endswith(ext) for ext in invalid_extensions) and
                not any(url.startswith(prefix) for prefix in invalid_prefixes) and
                any(path in url or url.endswith('.html') for path in valid_paths))

    def _find_parent(self, item):
        parent = item.xpath("./ancestor::li[contains(@class, 'toctree-l')]")
        if parent:
            return parent[-1].css("a.reference.internal::text").get(default=None)
        return None

    def get_priority(self, title: str) -> int:
        title = title.lower()
        if "tutorial" in title:
            return 1
        elif "library" in title or "reference" in title:
            return 2
        elif "howto" in title:
            return 3
        elif "glossary" in title or "search" in title:
            return 100
        else:
            return 4