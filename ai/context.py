import json
import os
import re
from pathlib import Path
from datetime import datetime

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(exist_ok=True)

MAX_HTML_SNAPSHOTS = 3
MAX_CHAT_MESSAGES = 20


class ProjectContext:
    """Holds all state for a single project, persisted to JSON."""

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id
        self.path = PROJECTS_DIR / f"{project_id}.json"
        self.data = self._load()

    # persistence
    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._empty()

    def _empty(self) -> dict:
        return {
            "project_id": self.project_id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "html_snapshots": [],  
            "structure": {           
                "title": None,
                "headings": [],      
                "paragraphs": [],    
                "buttons": [],       
                "links": [],         
                "inputs": [],        
                "images": [],        
                "list_items": [],    
                "colors": {
                    "background": None,
                    "text": None,
                    "primary": None,
                },
                "fonts": [],
                "sections": [],
                "has_nav": False,
                "has_footer": False,
                "has_form": False,
            },
            "history": [],
        }

    def save(self):
        self.data["updated_at"] = datetime.utcnow().isoformat()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # snapshots
    @property
    def latest_html(self) -> str:
        snaps = self.data.get("html_snapshots", [])
        return snaps[-1] if snaps else ""

    def add_snapshot(self, html: str):
        snaps = self.data.setdefault("html_snapshots", [])
        snaps.append(html)
        self.data["html_snapshots"] = snaps[-MAX_HTML_SNAPSHOTS:]
        self.refresh_structure()

    # history
    @property
    def history(self) -> list:
        return self.data.get("history", [])

    def add_history(self, role: str, text: str, mode: str = None):
        hist = self.data.setdefault("history", [])
        hist.append({
            "role": role,
            "parts": [{"text": text}],
            "mode": mode,
            "timestamp": datetime.utcnow().isoformat(),
        })
        self.data["history"] = hist[-MAX_CHAT_MESSAGES:]

    def set_history(self, new_history: list):
        """Used by summarise_history to replace all history with compressed version."""
        self.data["history"] = new_history

    # structure extraction
    def refresh_structure(self):
        """Parse the latest HTML and update structured state."""
        html = self.latest_html
        if not html:
            return

        s = self.data["structure"]

        def clean(text: str, max_words: int = 15) -> str:
            """Strip tags, collapse whitespace, truncate."""
            t = re.sub(r"<[^>]+>", "", text)
            t = re.sub(r"\s+", " ", t).strip()
            words = t.split()
            if len(words) > max_words:
                return " ".join(words[:max_words]) + "..."
            return t

        # title
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        s["title"] = title_m.group(1).strip() if title_m else None

        # headings
        headings = re.findall(r"<h[123][^>]*>(.*?)</h[123]>", html, re.DOTALL | re.IGNORECASE)
        s["headings"] = [clean(h) for h in headings if clean(h)][:15]

        # paragraphs
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
        s["paragraphs"] = [clean(p, max_words=12) for p in paragraphs if clean(p)][:15]

        # buttons and CTA labels
        buttons = re.findall(r"<button[^>]*>(.*?)</button>", html, re.DOTALL | re.IGNORECASE)
        # also treat <a> with button-like classes as buttons
        button_links = re.findall(r'<a\s+[^>]*class="[^"]*(?:btn|button|cta)[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)
        # also treat <input type="submit/button">
        submit_inputs = re.findall(r'<input[^>]*type=["\'](?:submit|button)["\'][^>]*value=["\']([^"\']+)["\']', html, re.IGNORECASE)
        all_buttons = [clean(b) for b in buttons + button_links if clean(b)] + submit_inputs
        s["buttons"] = list(dict.fromkeys(all_buttons))[:15]  # dedupe preserving order

        # links
        link_texts = re.findall(r"<a\s+[^>]*>(.*?)</a>", html, re.DOTALL | re.IGNORECASE)
        cleaned_links = [clean(l) for l in link_texts if clean(l)]
        # filter out ones we already captured as buttons
        s["links"] = list(dict.fromkeys(l for l in cleaned_links if l not in s["buttons"]))[:20]

        # inputs
        placeholders = re.findall(r'<(?:input|textarea)[^>]*placeholder=["\']([^"\']+)["\']', html, re.IGNORECASE)
        labels = re.findall(r"<label[^>]*>(.*?)</label>", html, re.DOTALL | re.IGNORECASE)
        input_names = re.findall(r'<input[^>]*name=["\']([^"\']+)["\']', html, re.IGNORECASE)
        all_inputs = placeholders + [clean(l) for l in labels if clean(l)] + input_names
        s["inputs"] = list(dict.fromkeys(all_inputs))[:15]

        # images
        alts = re.findall(r'<img[^>]*alt=["\']([^"\']+)["\']', html, re.IGNORECASE)
        s["images"] = list(dict.fromkeys(a.strip() for a in alts if a.strip()))[:15]

        # list items
        list_items = re.findall(r"<li[^>]*>(.*?)</li>", html, re.DOTALL | re.IGNORECASE)
        s["list_items"] = [clean(li, max_words=10) for li in list_items if clean(li)][:20]

        # background color
        bg_m = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8}|rgb[a]?\([^)]+\)|[a-z]+)", html, re.IGNORECASE)
        s["colors"]["background"] = bg_m.group(1) if bg_m else None

        # text color
        text_m = re.search(r"body[^{]*\{[^}]*?color\s*:\s*(#[0-9a-fA-F]{3,8}|rgb[a]?\([^)]+\)|[a-z]+)", html, re.IGNORECASE | re.DOTALL)
        s["colors"]["text"] = text_m.group(1) if text_m else None

        # fonts
        fonts = re.findall(r"font-family\s*:\s*([^;{}]+)", html, re.IGNORECASE)
        s["fonts"] = list({f.strip().strip("'\"").split(",")[0].strip() for f in fonts})[:5]

        # sections
        sections = set()
        for marker in re.findall(r"<!--\s*SECTION:\s*(\w+)", html, re.IGNORECASE):
            sections.add(marker.lower())
        for tag in ["header", "nav", "main", "footer", "aside", "section"]:
            if re.search(rf"<{tag}\b", html, re.IGNORECASE):
                sections.add(tag)
        s["sections"] = sorted(sections)

        s["has_nav"] = bool(re.search(r"<nav\b", html, re.IGNORECASE))
        s["has_footer"] = bool(re.search(r"<footer\b", html, re.IGNORECASE))
        s["has_form"] = bool(re.search(r"<form\b", html, re.IGNORECASE))

    # prompt helper
    def describe_state(self) -> str:
        """Return a compact human-readable summary of current project state for injection into prompts."""
        s = self.data["structure"]
        if not self.latest_html:
            return ""

        lines = ["Current project state (use this to find existing content to modify, not duplicate):"]
        if s["title"]:
            lines.append(f"- Page title: {s['title']}")
        if s["headings"]:
            lines.append(f"- Existing headings: {', '.join(repr(h) for h in s['headings'][:8])}")
        if s["paragraphs"]:
            lines.append(f"- Existing paragraphs: {', '.join(repr(p) for p in s['paragraphs'][:6])}")
        if s["buttons"]:
            lines.append(f"- Existing buttons/CTAs: {', '.join(repr(b) for b in s['buttons'][:10])}")
        if s["links"]:
            lines.append(f"- Existing links: {', '.join(repr(l) for l in s['links'][:10])}")
        if s["inputs"]:
            lines.append(f"- Form inputs: {', '.join(repr(i) for i in s['inputs'][:10])}")
        if s["images"]:
            lines.append(f"- Image alts: {', '.join(repr(a) for a in s['images'][:8])}")
        if s["list_items"]:
            lines.append(f"- List items: {', '.join(repr(li) for li in s['list_items'][:10])}")
        if s["colors"]["background"]:
            lines.append(f"- Background color: {s['colors']['background']}")
        if s["colors"]["text"]:
            lines.append(f"- Text color: {s['colors']['text']}")
        if s["fonts"]:
            lines.append(f"- Fonts in use: {', '.join(s['fonts'])}")
        if s["sections"]:
            lines.append(f"- Sections present: {', '.join(s['sections'])}")
        lines.append(f"- Has nav: {s['has_nav']}, Has footer: {s['has_footer']}, Has form: {s['has_form']}")
        return "\n".join(lines)

    # reset
    def reset(self):
        self.data = self._empty()
        if self.path.exists():
            self.path.unlink()

    def delete(self):
        """Fully delete the project file."""
        if self.path.exists():
            self.path.unlink()