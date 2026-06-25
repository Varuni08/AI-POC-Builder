"""
Fast deterministic CSS edits. Runs BEFORE the LLM for requests we can handle locally.

Philosophy:
- Only intercept requests we can handle 100% reliably
- If in doubt, return None and let the LLM handle it
- Use !important ONLY when overriding Tailwind utility classes is necessary
"""

import re


COLOR_MAP = {
    "red": "#ef4444", "dark red": "#991b1b", "light red": "#fca5a5",
    "crimson": "#DC143C", "scarlet": "#FF2400", "maroon": "#800000",
    "rose": "#FF007F", "ruby": "#9B111E",
    "pink": "#ec4899", "hot pink": "#FF69B4", "deep pink": "#FF1493",
    "light pink": "#FFB6C1", "blush": "#DE5D83", "salmon": "#FA8072",
    "coral": "#FF6B6B",
    "orange": "#f97316", "dark orange": "#FF8C00", "light orange": "#FFD580",
    "peach": "#FFCBA4", "tangerine": "#F28500", "amber": "#FFBF00",
    "yellow": "#f0b429", "gold": "#FFD700", "light yellow": "#FFFFE0",
    "lemon": "#FFF44F", "mustard": "#FFDB58",
    "green": "#22c55e", "dark green": "#15803d", "light green": "#86efac",
    "mint": "#98FF98", "mint green": "#98FF98", "lime": "#84cc16",
    "olive": "#808000", "emerald": "#50C878", "forest green": "#228B22",
    "sage": "#B2AC88", "teal": "#14b8a6", "dark teal": "#0f766e",
    "blue": "#3b82f6", "dark blue": "#1e40af", "light blue": "#93c5fd",
    "navy": "#001F3F", "dark navy": "#0a0a1a", "sky blue": "#87CEEB",
    "royal blue": "#4169E1", "cobalt": "#0047AB", "cerulean": "#007BA7",
    "powder blue": "#B0E0E6", "steel blue": "#4682B4",
    "purple": "#8b5cf6", "dark purple": "#5b21b6", "light purple": "#c4b5fd",
    "violet": "#7F00FF", "lavender": "#E6E6FA", "indigo": "#6366f1",
    "plum": "#DDA0DD", "mauve": "#E0B0FF", "lilac": "#C8A2C8",
    "periwinkle": "#CCCCFF",
    "white": "#FFFFFF", "black": "#000000",
    "grey": "#808080", "gray": "#808080",
    "dark grey": "#374151", "dark gray": "#374151",
    "light grey": "#d1d5db", "light gray": "#d1d5db",
    "charcoal": "#36454F", "slate": "#708090",
    "silver": "#C0C0C0", "off white": "#FAF9F6",
    "brown": "#92400e", "dark brown": "#3b1a08", "light brown": "#c6956a",
    "beige": "#F5F5DC", "cream": "#FFFDD0", "tan": "#D2B48C",
    "khaki": "#C3B091", "chocolate": "#7B3F00", "coffee": "#6F4E37",
    "caramel": "#C68642",
    "cyan": "#06b6d4", "aqua": "#00FFFF", "turquoise": "#40E0D0",
    "dark cyan": "#008B8B",
}

FONT_MAP = {
    "inter": "'Inter', sans-serif",
    "roboto": "'Roboto', sans-serif",
    "open sans": "'Open Sans', sans-serif",
    "lato": "'Lato', sans-serif",
    "poppins": "'Poppins', sans-serif",
    "montserrat": "'Montserrat', sans-serif",
    "nunito": "'Nunito', sans-serif",
    "raleway": "'Raleway', sans-serif",
    "dm sans": "'DM Sans', sans-serif",
    "outfit": "'Outfit', sans-serif",
    "georgia": "Georgia, serif",
    "times": "'Times New Roman', serif",
    "merriweather": "'Merriweather', serif",
    "playfair": "'Playfair Display', serif",
    "lora": "'Lora', serif",
    "courier": "'Courier New', monospace",
    "fira code": "'Fira Code', monospace",
    "jetbrains mono": "'JetBrains Mono', monospace",
}

# Helpers

def extract_color(prompt: str) -> str | None:
    p = prompt.lower()
    # hex first
    m = re.search(r"#([0-9a-fA-F]{3,8})", prompt)
    if m:
        return m.group(0)
    # named colors, longest match first
    for name in sorted(COLOR_MAP.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", p):
            return COLOR_MAP[name]
    return None


def extract_font(prompt: str) -> tuple[str, str] | None:
    """Return (css_value, google_font_name) or None. google_font_name is '' for generic families."""
    p = prompt.lower()

    # specific named fonts first (longest match wins)
    for name in sorted(FONT_MAP.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", p):
            css_value = FONT_MAP[name]
            google_name = re.search(r"'([^']+)'", css_value)
            return css_value, (google_name.group(1) if google_name else "")

    # generic family fallbacks
    if re.search(r"\b(?:serif|serif typeface|serif font)\b", p) and "sans" not in p:
        # pick a solid serif default
        return "'Lora', Georgia, serif", "Lora"
    if re.search(r"\bsans[-\s]?serif\b", p):
        return "'Inter', sans-serif", "Inter"
    if re.search(r"\b(?:monospace|mono|code font)\b", p):
        return "'Fira Code', monospace", "Fira Code"

    return None


def extract_quoted(prompt: str) -> str | None:
    for pat in [r'"([^"]+)"', r"'([^']+)'",
                r'[\u201c\u201d]([^\u201c\u201d]+)[\u201c\u201d]',
                r'[\u2018\u2019]([^\u2018\u2019]+)[\u2018\u2019]']:
        m = re.search(pat, prompt)
        if m:
            return m.group(1).strip()
    return None


def inject_css(html: str, css: str) -> str:
    """Append a <style> block near the end of <body> so it wins cascade order."""
    block = f'<style data-poc-edit>{css}</style>'
    if "</body>" in html:
        return html.replace("</body>", f"{block}\n</body>")
    if "</html>" in html:
        return html.replace("</html>", f"{block}\n</html>")
    return html + f"\n{block}"


def ensure_google_font(html: str, font_name: str) -> str:
    """Add a Google Fonts <link> in <head> if not already present."""
    if not font_name or font_name.lower() in html.lower():
        return html
    gf = font_name.replace(" ", "+")
    link = f'<link href="https://fonts.googleapis.com/css2?family={gf}:wght@400;500;600;700&display=swap" rel="stylesheet"/>'
    if "</head>" in html:
        return html.replace("</head>", f"  {link}\n</head>")
    return link + "\n" + html


HANDLERS = [
    # heading text change
    ("heading_text", [
        r"(?:change|update|set|make|replace|rename|rewrite)\s+(?:the\s+)?(?:main\s+|page\s+|hero\s+|blog\s+post\s+)?(?:heading|title|h1|headline)\s+(?:to|say|display|read|be|to\s+say|to\s+display|to\s+read)\b",
        r"\b(?:heading|title|h1|headline)\s+(?:should|to)\s+(?:be|say|read|display)\b",
    ]),
    # background color
    ("background", [
        r"\b(?:change|set|make|update)\s+(?:the\s+)?(?:background|bg)\b",
        r"\b(?:background|bg)\s+(?:color|colour)?\s*(?:to|is)\b",
    ]),
    # text color
    ("text_color", [
        r"\b(?:text|font)\s+colou?r\b",
        r"\btext\s+font\s+colou?r\b",
        r"\b(?:change|set|make)\s+(?:the\s+)?text\s+(?:to|colou?r)\b",
    ]),
    # button color
    ("button_color", [
        r"\bbutton\s+(?:color|colour|background)\b",
        r"\b(?:change|set|make)\s+(?:the\s+)?button\s+(?:to|color|colour|background)\b",
    ]),
    # font family
    ("font_family", [
        r"\b(?:use|set|change|make|switch\s+to)\s+(?:the\s+)?(?:font|typography|typeface)\b",
        r"\bfont\s+(?:family|to|is)\b",
    ]),
    # border radius
    ("radius", [
        r"\b(?:more|less)\s+rounded\b",
        r"\brounded\s+corners\b",
        r"\bborder[-\s]?radius\b",
        r"\bsharp\s+(?:corners|edges)\b",
    ]),
    # shadow toggle
    ("shadow", [
        r"\b(?:add|remove|more|less)\s+shadow\b",
        r"\bno\s+shadow\b",
    ]),
]


def can_handle(prompt: str) -> bool:
    p = prompt.lower()
    for _, patterns in HANDLERS:
        if any(re.search(pat, p) for pat in patterns):
            return True
    return False


def _match_handler(prompt: str) -> str | None:
    p = prompt.lower()
    for name, patterns in HANDLERS:
        if any(re.search(pat, p) for pat in patterns):
            return name
    return None

# Main edit dispatch

def apply_edit(prompt: str, html: str) -> str | None:
    """Return edited HTML, or None if this edit can't be handled deterministically."""
    handler = _match_handler(prompt)
    if not handler:
        return None

    if handler == "heading_text":
        new_text = extract_quoted(prompt)
        if not new_text:
            return None  # no quoted text, let LLM handle it
        print(f"[editor] heading text -> {new_text!r}")
        for tag in ("h1", "h2"):
            new_html, count = re.subn(
                rf"(<{tag}[^>]*>)(.*?)(</{tag}>)",
                lambda m: m.group(1) + new_text + m.group(3),
                html, count=1, flags=re.DOTALL | re.IGNORECASE
            )
            if count > 0:
                return new_html
        return None

    if handler == "background":
        c = extract_color(prompt)
        if not c:
            return None
        print(f"[editor] background -> {c}")
        return inject_css(html, f"body {{ background: {c} !important; background-image: none !important; }}")

    if handler == "text_color":
        c = extract_color(prompt)
        if not c:
            return None
        print(f"[editor] text color -> {c}")
        # override Tailwind text-* utilities
        return inject_css(html, f"body, body * {{ color: {c} !important; }}")

    if handler == "button_color":
        c = extract_color(prompt)
        if not c:
            return None
        print(f"[editor] button color -> {c}")
        # override Tailwind bg-* utilities on buttons
        css = (f"button, .btn, [class*='btn-'], [role='button'], "
               f"input[type='submit'], input[type='button'], a.button "
               f"{{ background: {c} !important; background-image: none !important; "
               f"border-color: {c} !important; }}")
        return inject_css(html, css)

    if handler == "font_family":
        f = extract_font(prompt)
        if not f:
            return None
        css_value, google_name = f
        print(f"[editor] font -> {css_value}")
        html = ensure_google_font(html, google_name)
        return inject_css(html, f"body {{ font-family: {css_value} !important; }}")

    if handler == "radius":
        p = prompt.lower()
        if "sharp" in p or "remove" in p or "no round" in p:
            radius = "0px"
        elif "more" in p or "very rounded" in p:
            radius = "16px"
        elif m := re.search(r"(\d+)\s*px", p):
            radius = m.group(1) + "px"
        else:
            radius = "12px"
        print(f"[editor] border radius -> {radius}")
        return inject_css(html, f"button, .card, [class*='card'], input, img {{ border-radius: {radius} !important; }}")

    if handler == "shadow":
        p = prompt.lower()
        if "remove" in p or "no shadow" in p or "less" in p:
            print("[editor] shadow -> none")
            return inject_css(html, "* { box-shadow: none !important; }")
        print("[editor] shadow -> add")
        return inject_css(html, ".card, [class*='card'], button, section { box-shadow: 0 4px 20px rgba(0,0,0,0.15); }")

    return None