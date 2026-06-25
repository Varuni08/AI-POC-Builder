import json
import re
from llm import call_llm

SYSTEM_PROMPT = """You are a UI designer who creates clean, modern HTML pages.

OUTPUT FORMAT:
- Return ONLY a complete HTML file
- First line: <!DOCTYPE html>
- Last line: </html>
- No markdown, no backticks, no explanations

STYLING:
- Use Tailwind CSS via CDN: <script src="https://cdn.tailwindcss.com"></script> in <head>
- Use Tailwind utility classes for styling (e.g. bg-gray-900, text-white, rounded-lg, flex, grid)
- If the user requests a specific theme (dark, light, colorful, minimal), follow it exactly
- Default to a dark theme only if the user has not specified otherwise
- Use Google Fonts when appropriate

LAYOUT:
- Use semantic HTML: <nav>, <main>, <section>, <footer>
- Sections should size to their content; use padding like py-12 or py-16, not min-h-screen
- Navigation should be a flex row: <nav class="flex items-center justify-between px-8 py-4">
- Use grids for cards: <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
- Make layouts responsive with Tailwind's sm:/md:/lg: breakpoints

IMAGES:
- For placeholder images use https://picsum.photos/800/400?random=N (vary N)
- Images: class="w-full h-48 object-cover rounded-lg"

Return only the HTML. Match the user's intent exactly.
"""


EDIT_SYSTEM_PROMPT = """You are editing an existing HTML page. Your job is ONLY to apply the user's requested change.

RULES:
- Preserve every existing class, style, color, font, and layout that the user did not ask to change
- If the user asks to change one thing, change ONLY that one thing
- If the user asks to add something, insert it at a sensible location without removing other content
- If the user asks to remove something, remove only that thing
- Keep the Tailwind CDN script and any Google Font links in place
- Output the complete HTML file, from <!DOCTYPE html> to </html>
- No markdown, no backticks, no explanations
"""


# Minimal cleanup 

def clean_llm_output(text: str) -> str:
    """Strip markdown fences, ensure DOCTYPE and closing tags. No style overrides."""
    # strip leading/trailing code fences
    text = re.sub(r"^`{3,}(?:html?)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?`{3,}\s*$", "", text)
    text = text.strip()

    # cut anything before <!DOCTYPE or <html
    doctype = re.search(r"<!DOCTYPE", text, re.IGNORECASE)
    html_tag = re.search(r"<html", text, re.IGNORECASE)
    if doctype:
        text = text[doctype.start():]
    elif html_tag:
        text = text[html_tag.start():]

    # ensure closing tag exists
    if not text.lower().strip().endswith("</html>"):
        text = text.rstrip() + "\n</html>"

    # ensure Tailwind is loaded (it's the only style framework we commit to)
    if "cdn.tailwindcss.com" not in text and "</head>" in text:
        text = text.replace(
            "</head>",
            '  <script src="https://cdn.tailwindcss.com"></script>\n</head>'
        )

    return text


# History management

def summarise_history(history: list) -> list:
    """Compress long history into a 2-turn summary."""
    print("Summarising history...")

    summary_prompt = f"""The following is a conversation where a UI was built step by step.
Summarise the CURRENT STATE of the UI in concise bullet points. Cover: layout, colors, fonts, components, and any specific values. Be precise.

Conversation:
{json.dumps(history)}"""

    summary = call_llm([{"role": "user", "parts": [{"text": summary_prompt}]}], mode="edit")

    compressed = [
        {"role": "user", "parts": [{"text": f"Summary of what we built so far:\n{summary}"}]},
        {"role": "model", "parts": [{"text": "Understood. I have full context. What would you like to change?"}]}
    ]
    print("History compressed.")
    return compressed


# Generation

def generate_full(prompt: str, history: list, latest_html: str, context_summary: str = "") -> str:
    """Create a new page or rewrite an existing one based on the user's prompt."""
    print(f"generate_full: has_html={bool(latest_html)}")

    if not latest_html:
        # fresh project
        user_msg = f"{prompt}\n\nReturn ONLY raw HTML starting with <!DOCTYPE html>."
        html = call_llm([{"role": "user", "parts": [{"text": user_msg}]}], SYSTEM_PROMPT, mode="new_page")
    else:
        # editing existing HTML
        context_block = f"\n\n{context_summary}" if context_summary else ""
        user_msg = f"""Current HTML:
{latest_html}
{context_block}

Change to make: {prompt}

Return the complete updated HTML file. Preserve everything the user did not ask to change."""
        html = call_llm([{"role": "user", "parts": [{"text": user_msg}]}], EDIT_SYSTEM_PROMPT, mode="edit")

    return clean_llm_output(html)


def generate_partial(prompt: str, section: str, latest_html: str, context_summary: str = "") -> str:
    """Regenerate a single marked section. Falls back to full regen if section markers aren't present."""
    print(f"generate_partial: section={section}")

    marker_pattern = rf"<!--\s*SECTION:\s*{section}\s*-->.*?<!--\s*END SECTION:\s*{section}\s*-->"
    m = re.search(marker_pattern, latest_html, re.DOTALL | re.IGNORECASE)

    if not m:
        print(f"Section '{section}' not marked — falling back to full regen.")
        return generate_full(prompt, [], latest_html, context_summary)

    old_section = m.group(0)
    context_block = f"\n\n{context_summary}" if context_summary else ""

    new_section_raw = call_llm([{
        "role": "user",
        "parts": [{"text": f"""You are redesigning the '{section}' section of an existing page.

Request: {prompt}
{context_block}

Current {section} section:
{old_section}

Return ONLY the updated section HTML, wrapped in <!-- SECTION: {section} --> and <!-- END SECTION: {section} --> comments. No explanation, no full page, no markdown."""}]
    }], mode="partial")

    new_section = new_section_raw.strip()
    if old_section in latest_html:
        return latest_html.replace(old_section, new_section)

    # failed replace, fall back
    return generate_full(prompt, [], latest_html, context_summary)

# Prompt improvement

def improve_prompt(prompt: str) -> str:
    """Ask the LLM to tighten up a vague prompt without inventing requirements."""
    response = call_llm([{
        "role": "user",
        "parts": [{"text": f"""Rewrite this UI prompt to be clearer and more specific. Keep it under 2 sentences.

User's request: "{prompt}"

Rules:
- If it's a SIMPLE EDIT (color, font, add/remove one element): return it almost as-is
- If it's a NEW PAGE request: add brief detail about layout and components
- Do NOT invent new requirements the user didn't mention
- Reply with ONLY the improved prompt, no preamble

Examples:
"make background mint green" -> "Change the background to mint green."
"a portfolio site" -> "A dark-themed portfolio with a hero, projects grid, and contact form."
"""}]
    }], mode="edit")
    return response.strip().strip('"').strip("'")


# Personalized response

def generate_response_message(prompt: str, is_edit: bool = False) -> str:
    """Generate a short, friendly, contextual response after a generation finishes.

    Falls back to a sensible default if the LLM call fails.
    """
    try:
        if is_edit:
            instruction = f"""The user just asked me to make this edit to their UI: "{prompt}"

Write a SHORT confirmation message (under 15 words) acknowledging the change. Be specific to what they asked, casual but professional. End with a brief follow-up question or invitation.

Examples:
"Done — swapped email for blood group. Anything else to tweak?"
"Footer added. Want to adjust its color or content?"
"Updated the button to coral. Looks good?"

Reply with ONLY the message, no quotes, no preamble."""
        else:
            instruction = f"""The user just asked me to build this UI: "{prompt}"

Write a SHORT confirmation message (under 20 words) saying it's ready. Mention 1-2 key sections you built. Casual but professional. End with a brief follow-up suggestion.

Examples:
"Your hospital booking system is ready — patient form, doctor list, and calendar view. Want to add confirmation flow?"
"Weather app done with current conditions and 7-day forecast. Try adding location search?"
"Built your portfolio with hero, projects, and contact form. Want to tweak the colors?"

Reply with ONLY the message, no quotes, no preamble."""

        response = call_llm(
            [{"role": "user", "parts": [{"text": instruction}]}],
            mode="edit"
        )
        msg = response.strip().strip('"').strip("'")
        # safety net: if model returned something weird/empty, use fallback
        if not msg or len(msg) > 200:
            raise ValueError("response out of bounds")
        return msg
    except Exception as e:
        print(f"[response message] fallback used: {e}")
        # graceful fallback
        if is_edit:
            return "Updated. Check the preview — anything else?"
        return "Your page is ready. Want to refine any sections?"

# Edit suggestions 

def _html_has(html: str, *keywords: str) -> bool:
    h = html.lower()
    return any(kw in h for kw in keywords)


def suggest_edits(prompt: str, html: str = "", history: list = []) -> list:
    p = prompt.lower()

    # anchor on first user message (original intent)
    user_messages = [h["parts"][0]["text"].lower() for h in history if h["role"] == "user"]
    first_intent = user_messages[0] if user_messages else p
    # Use ALL messages to detect what's been changed, not just last 4
    all_history = " ".join(user_messages + [p])
    all_context = first_intent + " " + p

    # what's already been styled/edited (across the whole session)
    changed_bg     = "background" in all_history or " bg " in all_history
    changed_font   = "font" in all_history or "typography" in all_history or "typeface" in all_history
    changed_color  = "color" in all_history or "colour" in all_history
    changed_radius = "rounded" in all_history or "corner" in all_history or "radius" in all_history
    changed_shadow = "shadow" in all_history
    changed_spacing = "spacing" in all_history or "padding" in all_history or "margin" in all_history

    # what's already in the page
    has_rounded      = _html_has(html, "border-radius", "rounded-")
    has_shadow       = _html_has(html, "box-shadow", "shadow-")
    has_footer       = _html_has(html, "<footer")
    has_search       = _html_has(html, 'type="search"', "search bar", "<input type='search'")
    has_sidebar      = _html_has(html, "<aside", "sidebar")
    has_pricing      = _html_has(html, "pricing", "per month", "/mo")
    has_animated     = _html_has(html, "animation", "transition-", "keyframes")
    has_chart        = _html_has(html, "<canvas", "chart", "graph")
    has_testimonials = _html_has(html, "testimonial", "what our customers")
    has_hover        = _html_has(html, ":hover", "hover:")
    has_password     = _html_has(html, 'type="password"')
    has_validation   = _html_has(html, "validation", "error-message")

    suggestions = []

    # first suggestions based on what's changed vs original intent
    if not changed_bg:
        suggestions.append("Change background to dark navy")
    elif not changed_font:
        suggestions.append("Use Poppins font")
    elif not changed_color:
        suggestions.append("Change button color to emerald")
    elif not changed_radius and not has_rounded:
        suggestions.append("Add rounded corners")
    elif not changed_shadow and not has_shadow:
        suggestions.append("Add card shadows")
    elif not changed_spacing:
        suggestions.append("Increase section spacing")

    # content suggestions based on original intent, more specific matching first
    if any(w in all_context for w in ["restaurant", "food menu", "cafe", "diner", "bistro"]):
        suggestions.append("Add price highlight badges")
        suggestions.append("Add category filter tabs")
    elif "weather" in all_context:
        suggestions.append("Add hourly forecast row")
        suggestions.append("Add wind speed display")
    elif any(w in all_context for w in ["todo", "task", "kanban"]):
        suggestions.append("Add priority color labels")
        if not has_animated:
            suggestions.append("Add task completion animation")
    elif "portfolio" in all_context:
        suggestions.append("Add a skills section")
        if not has_hover:
            suggestions.append("Add project hover effects")
    elif any(w in all_context for w in ["dashboard", "analytics", "admin"]):
        if not has_sidebar:
            suggestions.append("Add a dark sidebar")
        if not has_chart:
            suggestions.append("Add data charts")
    elif any(w in all_context for w in ["blog", "article", "news"]):
        if not has_search:
            suggestions.append("Add a search bar")
        suggestions.append("Add category tags")
    elif any(w in all_context for w in ["login", "signup", "auth", "sign in", "register"]):
        if has_password:
            suggestions.append("Add show/hide password toggle")
        if not has_validation:
            suggestions.append("Add form validation messages")
    elif any(w in all_context for w in ["landing", "saas", "product", "marketing", "startup"]):
        if not has_pricing:
            suggestions.append("Add a pricing section")
        if not has_testimonials:
            suggestions.append("Add customer testimonials")
    else:
        if not has_hover:
            suggestions.append("Add hover animations")
        if not has_footer:
            suggestions.append("Add a footer section")

    # fallback if we didn't get enough
    if len(suggestions) < 2:
        if not has_footer:
            suggestions.append("Add a footer section")
        if not has_hover:
            suggestions.append("Add hover effects to cards")
        if not changed_spacing:
            suggestions.append("Increase section spacing")
        else:
            suggestions.append("Add subtle border accents")

    # filter out suggestions that closely match anything the user already asked for
    def already_asked(suggestion: str) -> bool:
        s = suggestion.lower()
        # extract the meaningful keywords (skip first verb)
        keywords = [w for w in s.split() if len(w) > 3 and w not in {"add", "change", "make", "increase", "with", "color", "color"}]
        for msg in user_messages:
            # if 2+ keywords from this suggestion appeared in a previous message, skip it
            matches = sum(1 for k in keywords if k in msg)
            if matches >= 2:
                return True
        return False

    suggestions = [s for s in suggestions if not already_asked(s)]

    # dedupe preserving order
    seen = set()
    out = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:3]