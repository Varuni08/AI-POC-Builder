import asyncio
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from editor import can_handle, apply_edit
from context import ProjectContext
from .logic import (
    generate_full,
    generate_partial,
    summarise_history,
    improve_prompt,
    suggest_edits,
    generate_response_message,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static file serving
FRONTEND_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = FRONTEND_DIR / "index.html"


@app.get("/")
async def serve_index():
    """Serve the frontend."""
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    raise HTTPException(status_code=404, detail="index.html not found")


# per-user context cache 
_user_contexts: dict[str, ProjectContext] = {}


def get_ctx(user_id: str) -> ProjectContext:
    """Get or lazy-load a user's project context."""
    if user_id not in _user_contexts:
        # sanitize: only allow safe chars in filenames
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', user_id)[:64] or "default"
        _user_contexts[safe_id] = ProjectContext(safe_id)
        return _user_contexts[safe_id]
    return _user_contexts[user_id]


def extract_user_id(x_user_id: str | None) -> str:
    """Pull user id from header, fall back to 'default' if missing."""
    if not x_user_id:
        return "default"
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', x_user_id)[:64]
    return safe or "default"


# ---- Rate limiting: per-user OpenAI call cap per day ----
MAX_CALLS_PER_DAY = 50
_rate_limits: dict[str, dict] = {}  


def check_rate_limit(user_id: str) -> None:
    """Raise HTTPException if user is over their daily quota."""
    today = str(date.today())
    entry = _rate_limits.get(user_id)
    if not entry or entry["date"] != today:
        _rate_limits[user_id] = {"date": today, "count": 0}
        entry = _rate_limits[user_id]
    if entry["count"] >= MAX_CALLS_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {MAX_CALLS_PER_DAY} generations reached. Try again tomorrow."
        )
    entry["count"] += 1


# async lock (prevents concurrent LLM calls)
generate_lock = asyncio.Lock()


def is_partial_request(prompt: str) -> dict:
    prompt_lower = prompt.lower()

    property_keywords = [
        "color", "colour", "background", "font", "typography",
        "title", "heading", "text", "button", "border", "padding",
        "margin", "spacing", "size", "weight", "style", "theme",
        "dark mode", "light mode", "shadow", "radius", "rounded",
        "animation", "transition", "hover", "opacity", "gradient",
        "image", "icon", "logo", "align", "layout", "width", "height"
    ]

    sections = {
        "hero": ["hero", "banner", "header section", "top section", "hero section"],
        "navbar": ["navbar", "nav bar", "navigation", "menu bar", "nav menu"],
        "footer": ["footer", "bottom section", "footer section"],
        "features": ["features", "feature section", "feature cards", "features section"],
        "pricing": ["pricing", "price section", "plans", "pricing section"],
        "testimonials": ["testimonials", "reviews section", "testimonial section"],
        "contact": ["contact form", "contact section", "contact us"],
        "sidebar": ["sidebar", "side panel", "side bar"],
        "cards": ["cards section", "card grid", "card section"],
        "form": ["form", "input", "fields", "signup form", "login form"],
        "table": ["table", "data table", "grid"],
        "chart": ["chart", "graph", "plot"],
    }

    modify_words = [
        "change", "update", "modify", "edit", "fix", "make",
        "replace", "set", "use", "switch", "turn", "apply",
        "adjust", "increase", "decrease", "move", "rename", "rewrite"
    ]
    # 'add' is intentionally NOT here — "add a pricing section" means insert new,
    # which requires seeing the full HTML, so it's a full edit not a section swap

    add_words = ["add", "insert", "include", "append"]
    remove_words = ["remove", "delete"]

    has_modify_intent = any(word in prompt_lower for word in modify_words)
    has_add_intent = any(word in prompt_lower for word in add_words)
    has_remove_intent = any(word in prompt_lower for word in remove_words)

    # If user is adding or removing a section, that's a full edit 
    if has_add_intent or has_remove_intent:
        return {"type": "full", "section": None}

    # Property change scoped to a specific section = partial
    if any(kw in prompt_lower for kw in property_keywords) and has_modify_intent:
        for section, keywords in sections.items():
            if any(kw in prompt_lower for kw in keywords):
                return {"type": "partial", "section": section}
        return {"type": "full", "section": None}

    # Modify intent + section keyword = partial
    for section, keywords in sections.items():
        if any(kw in prompt_lower for kw in keywords) and has_modify_intent:
            return {"type": "partial", "section": section}

    return {"type": "full", "section": None}


def is_vague_prompt(prompt: str, has_context: bool = False) -> bool:
    """
    Returns True if the prompt is too vague to generate from.

    Logic: prompts are valid by default. Only flagged vague when they are
    genuinely empty, filler-only, or extremely short without any signal.
    """
    p = prompt.lower().strip()
    # remove punctuation, normalize whitespace
    words = [w.strip("!?.,;:'\"") for w in p.split()]
    words = [w for w in words if w]

    # empty or ultra-short
    if len(words) == 0 or len(p) < 4:
        return True

    # greetings / filler / stall words
    filler_only = {
        "hi", "hello", "hey", "yo", "sup", "test", "testing",
        "ok", "okay", "sure", "yes", "yep", "yeah", "alright",
        "go", "start", "begin", "proceed", "continue", "next",
        "idk", "idc", "dunno", "hmm", "hm", "whatever",
        "anything", "something", "random"
    }
    if all(w in filler_only for w in words):
        return True

    # If the prompt is reasonably detailed (5+ words), it's valid.
    # Real users don't write 5-word prompts that are vague.
    if len(words) >= 5:
        return False

    # If the user has existing HTML and the prompt is 2+ words, it's an edit.
    # Edit prompts can be short like "make it dark" or "remove footer".
    if has_context and len(words) >= 2:
        return False

    # Short prompts (2-4 words) without context — only valid if they contain
    # a recognizable UI noun.
    ui_nouns = {
        "website", "webpage", "landing", "dashboard", "portfolio", "blog",
        "shop", "store", "ecommerce", "e-commerce", "gallery", "profile",
        "app", "tool", "tracker", "planner", "calculator", "converter",
        "menu", "restaurant", "cafe", "login", "signup", "form",
        "todo", "kanban", "chat", "messenger", "weather", "news",
        "pricing", "checkout", "cart", "player", "podcast",
        "calendar", "timeline", "hero", "page", "site",
    }
    if any(word in ui_nouns for word in words):
        return False

    return True


def get_clarifying_question(prompt: str) -> str:
    p = prompt.lower()

    if any(w in p for w in ["website", "page", "site", "landing"]):
        return "What's the website for? For example: a product, personal portfolio, restaurant, startup, or blog?"
    if any(w in p for w in ["app", "tool", "dashboard", "tracker"]):
        return "What does the app do? For example: manage tasks, show analytics, track fitness, or something else?"
    if any(w in p for w in ["dark", "light", "minimal", "modern", "clean", "beautiful", "cool", "nice"]):
        return "Got the style! But what should it be — a landing page, dashboard, login form, blog, portfolio, or something else?"
    if any(w in p for w in ["business", "company", "startup", "brand"]):
        return "What industry or product is this for? For example: SaaS, e-commerce, agency, restaurant, or something else?"
    if any(w in p for w in ["something", "anything", "whatever", "idk", "random", "surprise"]):
        return "I need a bit more — a landing page, dashboard, login form, blog, portfolio, or something else?"
    if any(w in p for w in ["form", "login", "signup", "auth"]):
        return "What's the form for? Login only, signup only, or both on the same page?"
    if len(prompt.split()) <= 2:
        return f'"{prompt}" is a bit short — what kind of UI do you want? For example: a landing page, dashboard, login form, or blog?'

    return "Could you add a bit more detail? What kind of page is it and what should it include?"


# request models
class GenerateRequest(BaseModel):
    prompt: str

class ImproveRequest(BaseModel):
    prompt: str

class SuggestRequest(BaseModel):
    prompt: str


# endpoints

def is_new_project_prompt(prompt: str) -> bool:
    """Detect if the user's prompt describes a brand-new site/app rather than an edit."""
    p = prompt.lower().strip()

    # Clear 'start fresh' phrases
    fresh_phrases = [
        "start over", "start fresh", "new project", "from scratch",
        "reset", "clear", "begin again", "start new"
    ]
    if any(phrase in p for phrase in fresh_phrases):
        return True

    # Site-type nouns that describe a whole app/site
    site_types = [
        "landing page", "landing site", "portfolio", "portfolio site",
        "dashboard", "blog", "blog homepage", "blog site",
        "ecommerce", "e-commerce", "online store", "shop",
        "login page", "signup page", "auth page",
        "todo app", "todo list", "kanban", "kanban board",
        "weather app", "chat app", "chat ui",
        "music player", "video player",
        "restaurant menu", "food menu",
        "saas website", "saas landing", "saas site",
        "product page", "pricing page",
        "news site", "magazine site",
    ]

    # Creation verbs at the start of the prompt
    creation_starters = [
        "a ", "an ", "build ", "create ", "make ", "design ",
        "build me ", "create me ", "make me ", "i want ", "i need ",
        "generate ", "build a ", "create a ", "make a "
    ]

    starts_with_creation = any(p.startswith(v) for v in creation_starters)
    mentions_site_type = any(t in p for t in site_types)

    # "A blog homepage with..." or "Build a SaaS landing page..." - new project
    return starts_with_creation and mentions_site_type


@app.post("/generate")
async def generate(req: GenerateRequest, x_user_id: str = Header(default=None)):
    user_id = extract_user_id(x_user_id)
    ctx = get_ctx(user_id)

    if generate_lock.locked():
        raise HTTPException(status_code=429, detail="Still generating, please wait.")

    async with generate_lock:
        try:
            print(f"[user={user_id}] Generate request: {req.prompt}")

            # If user describes a brand new site type, reset the stale context first
            if ctx.latest_html and is_new_project_prompt(req.prompt):
                print("Detected new project prompt — resetting stale context.")
                ctx.reset()

            # Always ask for clarification on vague prompts — you can't edit with "surprise me"
            if is_vague_prompt(req.prompt, has_context=bool(ctx.latest_html)):
                return {
                    "html": "",
                    "mode": "clarify",
                    "question": get_clarifying_question(req.prompt)
                }

            # summarise history if getting long
            if len(ctx.history) > 6:
                compressed = summarise_history(ctx.history)
                ctx.set_history(compressed)
                ctx.save()

            intent = is_partial_request(req.prompt)
            context_summary = ctx.describe_state()

            print(f"can_handle: {can_handle(req.prompt)}, has_html: {bool(ctx.latest_html)}, intent: {intent}")

            if ctx.latest_html and can_handle(req.prompt):
                edited = apply_edit(req.prompt, ctx.latest_html)
                if edited:
                    html = edited
                    mode = "css_edit"
                    print("CSS edit applied instantly.")
                else:
                    check_rate_limit(user_id)
                    if intent["type"] == "partial" and intent["section"]:
                        loop = asyncio.get_event_loop()
                        html = await asyncio.wait_for(
                            loop.run_in_executor(None, generate_partial, req.prompt, intent["section"], ctx.latest_html, context_summary),
                            timeout=600
                        )
                        mode = "partial"
                    else:
                        loop = asyncio.get_event_loop()
                        html = await asyncio.wait_for(
                            loop.run_in_executor(None, generate_full, req.prompt, ctx.history, ctx.latest_html, context_summary),
                            timeout=600
                        )
                        mode = "full"

            elif ctx.latest_html and intent["type"] == "partial" and intent["section"]:
                check_rate_limit(user_id)
                loop = asyncio.get_event_loop()
                html = await asyncio.wait_for(
                    loop.run_in_executor(None, generate_partial, req.prompt, intent["section"], ctx.latest_html, context_summary),
                    timeout=600
                )
                mode = "partial"

            else:
                check_rate_limit(user_id)
                loop = asyncio.get_event_loop()
                html = await asyncio.wait_for(
                    loop.run_in_executor(None, generate_full, req.prompt, ctx.history, ctx.latest_html, context_summary),
                    timeout=600
                )
                mode = "full"

            # save to context
            ctx.add_history("user", req.prompt, mode=mode)
            ctx.add_history("model", html, mode=mode)
            ctx.add_snapshot(html)
            ctx.save()

            # Generate a personalized response message 
            was_first_gen = len(ctx.data.get("html_snapshots", [])) <= 1
            loop = asyncio.get_event_loop()
            try:
                response_msg = await asyncio.wait_for(
                    loop.run_in_executor(None, generate_response_message, req.prompt, not was_first_gen),
                    timeout=15
                )
            except Exception as e:
                print(f"[response_msg] using fallback: {e}")
                response_msg = "Updated. Check the preview — anything else?" if not was_first_gen else "Your page is ready. Want to refine any sections?"

            print(f"[user={user_id}] Done. Mode={mode}, HTML length={len(html)}")
            return {"html": html, "mode": mode, "message": response_msg}

        except Exception as e:
            print(f"Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/improve-prompt")
async def improve(req: ImproveRequest, x_user_id: str = Header(default=None)):
    user_id = extract_user_id(x_user_id)
    ctx = get_ctx(user_id)

    if is_vague_prompt(req.prompt, has_context=bool(ctx.latest_html)):
        return {
            "improved": "",
            "mode": "clarify",
            "question": get_clarifying_question(req.prompt)
        }

    if generate_lock.locked():
        raise HTTPException(status_code=429, detail="Still generating, please wait.")

    async with generate_lock:
        try:
            print(f"Improving prompt: {req.prompt}")
            loop = asyncio.get_event_loop()
            improved = await asyncio.wait_for(
                loop.run_in_executor(None, improve_prompt, req.prompt),
                timeout=60
            )
            # If Ollama returned nothing useful, just return the original
            if not improved or not improved.strip():
                improved = req.prompt
            return {"improved": improved}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Improve prompt timed out")
        except Exception as e:
            print(f"[improve ERROR] {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/suggest-edits")
async def suggest(req: SuggestRequest, x_user_id: str = Header(default=None)):
    user_id = extract_user_id(x_user_id)
    ctx = get_ctx(user_id)
    try:
        print(f"[user={user_id}] Getting suggestions for: {req.prompt}")
        suggestions = suggest_edits(req.prompt, ctx.latest_html, ctx.history)
        return {"suggestions": suggestions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset")
async def reset(x_user_id: str = Header(default=None)):
    user_id = extract_user_id(x_user_id)
    ctx = get_ctx(user_id)
    ctx.reset()
    # also clear cached object so next call re-reads fresh
    _user_contexts.pop(user_id, None)
    print(f"[user={user_id}] Session reset.")
    return {"ok": True}


@app.get("/state")
async def get_state(x_user_id: str = Header(default=None)):
    """Debug endpoint — view current project state for this user."""
    user_id = extract_user_id(x_user_id)
    ctx = get_ctx(user_id)
    return {
        "user_id": user_id,
        "project_id": ctx.project_id,
        "structure": ctx.data["structure"],
        "history_length": len(ctx.history),
        "snapshot_count": len(ctx.data.get("html_snapshots", [])),
        "latest_html_length": len(ctx.latest_html),
        "calls_today": _rate_limits.get(user_id, {}).get("count", 0),
        "daily_limit": MAX_CALLS_PER_DAY,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/cost")
async def cost():
    """View current OpenAI spend."""
    try:
        from llm import get_cost_summary
        return get_cost_summary()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[/cost ERROR] {e}\n{tb}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/cost/reset")
async def reset_cost():
    """Zero out cost tracking."""
    from llm import reset_cost_tracking
    reset_cost_tracking()
    return {"ok": True}