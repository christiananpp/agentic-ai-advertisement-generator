# app_crewai.py — CrewAI Poster (clean, patched)

import os, json, re, math
from io import BytesIO
from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import urlparse, parse_qs, unquote

from dotenv import load_dotenv
import requests
from PIL import (
    Image, ImageDraw, ImageFont,
    ImageChops, ImageFilter, ImageStat
)
import streamlit as st
from streamlit_drawable_canvas import st_canvas

# ============================================================
# ========== ENV & CONFIG ====================================
# ============================================================

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

st.set_page_config(
    page_title="CrewAI Poster — Smart Overlay Designer",
    layout="wide"
)

# ============================================================
# ========== FONT UPLOAD & LOADERS ===========================
# ============================================================

st.sidebar.header("🅰️ Fonts")
os.makedirs("fonts", exist_ok=True)

TITLE_FONT_PATH = "fonts/PlayfairDisplay-Bold.ttf"
BODY_FONT_PATH  = "fonts/Inter-Regular.ttf"

tf = st.sidebar.file_uploader(
    "Upload Title Font (.ttf/.otf)", type=["ttf", "otf"]
)
bf = st.sidebar.file_uploader(
    "Upload Body Font (.ttf/.otf)", type=["ttf", "otf"]
)

if tf:
    with open("fonts/_uploaded_title.ttf", "wb") as f:
        f.write(tf.read())
    TITLE_FONT_PATH = "fonts/_uploaded_title.ttf"

if bf:
    with open("fonts/_uploaded_body.ttf", "wb") as f:
        f.write(bf.read())
    BODY_FONT_PATH = "fonts/_uploaded_body.ttf"


def get_font_title(size: int):
    for p in [
        TITLE_FONT_PATH,
        "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_font_body(size: int):
    for p in [
        BODY_FONT_PATH,
        "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ============================================================
# ========== SMART TITLE FORMATTER ===========================
# ============================================================

def format_title_for_poster(raw: str) -> str:
    """
    Shorten + aesthetic 1–2 line fashion title.
    Example input:
        "Elevate Your Wardrobe with Our Premium Blue Cotton Shirt"
    Example output:
        "WOMEN'S\nCOTTON SHIRT"
    """
    if not raw:
        return ""

    import re as _re
    s = raw.strip()

    # remove filler words
    remove_words = [
        r"\belevate\b", r"\bdiscover\b", r"\bexperience\b", r"\bwith our\b",
        r"\bpremium\b", r"\bexclusive\b", r"\bupgrade\b", r"\bperfect\b",
        r"\bcomfort\b", r"\bgame\b", r"\bperformance\b", r"\bshop\b",
        r"\bwardrobe\b", r"\bstyle\b",
    ]
    for w in remove_words:
        s = _re.sub(w, "", s, flags=_re.I)

    tokens = _re.findall(r"[A-Za-z']+", s)

    whitelist = [
        "women", "woman", "women’s", "womens",
        "men", "shirt", "tshirt", "tee",
        "sport", "sports", "cotton", "activewear", "top",
    ]
    keywords: List[str] = [
        t.upper() for t in tokens if t.lower() in whitelist
    ]

    # fallback kalau AI kasih judul aneh
    if not keywords:
        keywords = [t.upper() for t in tokens[:3]]

    title = " ".join(keywords).strip()
    if not title:
        return ""

    # split ke max 2 baris, ~14 char per baris
    words = title.split()
    lines: List[str] = []
    cur = ""

    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= 14:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    return "\n".join(lines[:2])

# ============================================================
# ========== IMAGE UTILS =====================================
# ============================================================

def _normalize_direct_image_url(url: str) -> str:
    # Bing redirect
    if "bing.com/images/" in url:
        try:
            qs = parse_qs(urlparse(url).query)
            murl = qs.get("mediaurl", [None])[0]
        except Exception:
            murl = None
        if murl:
            return unquote(murl)

    # Google Drive
    m = re.search(r"/d/([A-Za-z0-9_-]+)", url)
    if "drive.google.com" in url and m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url)
    if "drive.google.com" in url and m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    # Dropbox
    if "dropbox.com" in url and "dl=" in url:
        return url.replace("dl=0", "dl=1")

    # GitHub raw
    if "github.com" in url and "/blob/" in url:
        return (
            url.replace("https://github.com/", "https://raw.githubusercontent.com/")
               .replace("/blob/", "/")
        )

    return url


def load_image_from_url_or_path(src: str, fallback_size=(800, 600)) -> Image.Image:
    """Load image safely with placeholder fallback."""
    def placeholder(reason: str = "") -> Image.Image:
        img = Image.new("RGBA", fallback_size, (232, 232, 232, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 10), f"IMAGE PLACEHOLDER\n{reason}", fill=(80, 80, 80))
        return img

    if not src:
        return placeholder("empty")

    # local file
    if not src.startswith("http"):
        try:
            return Image.open(src).convert("RGBA")
        except Exception as e:
            return placeholder(f"local:{e}")

    # url
    try:
        url = _normalize_direct_image_url(src)
        r = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
            stream=True,
        )
        r.raise_for_status()
        content = r.content
        ct = r.headers.get("Content-Type", "").lower()

        # SVG → PNG
        if url.lower().endswith(".svg") or "svg" in ct:
            try:
                import cairosvg
                png = cairosvg.svg2png(bytestring=content)
                return Image.open(BytesIO(png)).convert("RGBA")
            except Exception:
                pass

        return Image.open(BytesIO(content)).convert("RGBA")

    except Exception as e:
        return placeholder(f"req:{e}")


def _unsplash_search(q: str, count: int = 1) -> List[str]:
    """Small helper to fetch product image from Unsplash."""
    if not UNSPLASH_ACCESS_KEY or not q:
        return []
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={
                "Accept-Version": "v1",
                "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
            },
            params={"query": q, "per_page": count},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            return [x["urls"]["regular"] for x in data.get("results", [])]
    except Exception:
        pass
    return []

# ============================================================
# ========== GRID HELPERS ====================================
# ============================================================

def _area_to_xyxy(a: Dict[str, int], cw: float, ch: float, g: int) -> Tuple[int, int, int, int]:
    x0 = int((a["col_start"] - 1) * (cw + g))
    y0 = int((a["row_start"] - 1) * (ch + g))
    x1 = int(a["col_end"] * cw + (a["col_end"] - 1) * g)
    y1 = int(a["row_end"] * ch + (a["row_end"] - 1) * g)
    return x0, y0, x1, y1


def _paste_with_fit(canvas: Image.Image, img: Image.Image, box, fit="contain"):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return

    rw, rh = bw / img.width, bh / img.height
    r = min(rw, rh) if fit == "contain" else max(rw, rh)

    resized = img.resize(
        (max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS
    )

    px = x0 + (bw - resized.width) // 2
    py = y0 + (bh - resized.height) // 2
    canvas.paste(resized, (px, py), resized if resized.mode == "RGBA" else None)


# ============================================================
# ========== LOGO OVERLAY ENGINE =============================
# ============================================================

def _apply_overlay(canvas: Image.Image, overlay: Image.Image, tbox,
                   *, pos="bottom-right", size_ratio=0.2,
                   offset=(0, 0), stack=None):
    tx0, ty0, tx1, ty1 = tbox
    tw, th = tx1 - tx0, ty1 - ty0
    if tw <= 0 or th <= 0:
        return

    base_w = max(1, int(tw * size_ratio))
    r = base_w / max(1, overlay.width)
    base = overlay.resize(
        (base_w, max(1, int(overlay.height * r))), Image.LANCZOS
    )

    def place(img, pos, off=(0, 0)):
        w, h = img.width, img.height
        if pos in ("br", "bottom-right"):
            x = tx1 - w + off[0]
            y = ty1 - h + off[1]
        elif pos in ("bl", "bottom-left"):
            x = tx0 + off[0]
            y = ty1 - h + off[1]
        elif pos in ("tl", "top-left"):
            x = tx0 + off[0]
            y = ty0 + off[1]
        elif pos in ("tr", "top-right"):
            x = tx1 - w + off[0]
            y = ty0 + off[1]
        else:  # center
            x = tx0 + (tw - w) // 2 + off[0]
            y = ty0 + (th - h) // 2 + off[1]

        canvas.paste(img, (int(x), int(y)), img)

    # stacking
    if stack and stack.get("count", 1) > 1:
        mode = stack.get("mode", "pile")
        count = int(stack.get("count", 2))
        gap = int(stack.get("gap", 12))
        dx, dy = stack.get("delta", [-10, 10])

        if mode == "row":
            for i in range(count):
                place(base, pos, (offset[0] + i*(base.width + gap), offset[1]))
        elif mode == "column":
            for i in range(count):
                place(base, pos, (offset[0], offset[1] + i*(base.height + gap)))
        else:  # pile
            for i in range(count):
                place(base, pos, (offset[0] + i*dx, offset[1] + i*dy))
    else:
        place(base, pos, offset)


# ============================================================
# ========== RENDER FROM GRID ================================
# ============================================================

def render_from_grid(graph: Dict[str, Any], save_path="dataset/poster.png"):
    W, H = graph["canvas"]["width"], graph["canvas"]["height"]
    cols, rows, g = graph["grid"]["cols"], graph["grid"]["rows"], graph["grid"]["gutter"]

    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    cw = (W - (cols - 1) * g) / cols
    ch = (H - (rows - 1) * g) / rows

    comp_boxes = {}

    # PASS 1 — Render text + images (excluding overlay logo)
    for comp in graph["components"]:
        cid = comp["id"]
        a = comp["area"]
        box = _area_to_xyxy(a, cw, ch, g)
        comp_boxes[cid] = box
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0

        if comp["type"] == "image" and cid != "logo":
            img = load_image_from_url_or_path(comp.get("src", ""), (bw, bh))
            _paste_with_fit(canvas, img, box, fit=comp.get("fit", "contain"))

        elif comp["type"] == "text":
            text = comp.get("content", "")
            align = comp.get("align", "left")

            fs = 92 if cid == "title" else 44
            font = get_font_title(fs) if cid == "title" else get_font_body(fs)

            while font.getbbox(text)[2] > bw and fs > 20:
                fs -= 2
                font = get_font_title(fs) if cid == "title" else get_font_body(fs)

            tw = font.getbbox(text)[2]
            tx = (
                x0 + (bw - tw) // 2
                if align == "center"
                else x1 - tw if align == "right"
                else x0
            )
            ty = y0 + 8
            draw.text((tx, ty), text, fill=(20, 20, 20, 255), font=font)

    # PASS 2 — Apply logo overlay OR regular logo placement
    for comp in graph["components"]:
        if comp["id"] == "logo" and comp["type"] == "image":
            logo = load_image_from_url_or_path(comp.get("src", ""), (260, 260))

            # auto-crop transparent padding
            if logo.mode == "RGBA":
                bbox = logo.split()[-1].getbbox()
                if bbox:
                    logo = logo.crop(bbox)

            tid = comp.get("overlay_target")

            if tid and tid in comp_boxes:
                _apply_overlay(
                    canvas,
                    logo,
                    comp_boxes[tid],
                    pos=comp.get("overlay_pos", "bottom-right"),
                    size_ratio=float(comp.get("overlay_size_ratio", 0.18)),
                    offset=tuple(comp.get("overlay_offset", [0, 0])),
                    stack=comp.get("overlay_stack"),
                )
            else:
                box = comp_boxes["logo"]
                _paste_with_fit(canvas, logo, box, fit="contain")

    out = canvas.convert("RGB")
    out.save(save_path)
    return out


# ============================================================
# ========== POLISH ENGINE ===================================
# ============================================================

def _rounded_mask(size, r):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size[0], size[1]), radius=r, fill=255)
    return m


def _drop_shadow(img, blur=12, offset=(0, 8), alpha=120):
    a = img.split()[-1] if img.mode == "RGBA" else Image.new("L", img.size, 255)
    spread = a.filter(ImageFilter.MaxFilter(7))

    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.putalpha(spread.point(lambda p: alpha if p > 0 else 0))

    sh = sh.filter(ImageFilter.GaussianBlur(blur))

    out = Image.new(
        "RGBA",
        (img.width + abs(offset[0]), img.height + abs(offset[1])),
        (0, 0, 0, 0),
    )
    out.alpha_composite(sh, (max(0, offset[0]), max(0, offset[1])))
    out.alpha_composite(img, (0, 0))
    return out


def _stroke(img, st=2, color=(255, 255, 255, 220)):
    a = img.split()[-1]
    dil = a.filter(ImageFilter.MaxFilter(size=st * 2 + 1))

    b = Image.new("RGBA", img.size, color)
    b.putalpha(ImageChops.subtract(dil, a))

    return Image.alpha_composite(b, img)


def _contrast(img: Image.Image, factor: float):
    if factor == 1.0:
        return img

    mean = ImageStat.Stat(img.convert("L")).mean[0]
    lut = [max(0, min(255, int(mean + (i - mean) * factor))) for i in range(256)]

    return Image.merge(
        "RGBA",
        [img.split()[i].point(lut if i < 3 else list(range(256))) for i in range(4)],
    )


def render_polished(
    graph: Dict[str, Any],
    factual: Dict[str, Any],
    *,
    contrast=1.05,
    grain=18,
    light=1.0,
    save_path="dataset/poster_polished.png",
):
    W, H = graph["canvas"]["width"], graph["canvas"]["height"]

    out = render_from_grid(graph, save_path)
    img = Image.open(save_path).convert("RGBA")

    img = _contrast(img, contrast)
    img = img.convert("RGB")

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    img.save(save_path, dpi=(96, 96))

    return img

# ============================================================
# ========== CREWAI AGENTS + JSON PARSER ======================
# ============================================================

from crewai import Agent, Task, Crew, Process
from langchain_openai import ChatOpenAI

# ---------------------- JSON SANITIZER ----------------------

def to_json(x):
    """Very robust json extractor."""
    if isinstance(x, dict):
        return x
    if not x:
        return {}

    s = str(x).strip()

    # Remove code fences
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.MULTILINE)

    # Extract only JSON-like block
    m = re.search(r"\{.*\}", s, flags=re.S)
    if not m:
        return {}

    s = m.group(0)

    # normalize quotes
    s = (
        s.replace("\u201c", '"')
         .replace("\u201d", '"')
         .replace("\u2018", '"')
         .replace("\u2019", '"')
    )
    s = re.sub(r",\s*([}\]])", r"\1", s)

    try:
        import json5
        return json5.loads(s)
    except:
        pass

    try:
        return json.loads(s)
    except:
        return {}


# ============================================================
# ========== DEFAULT COMPONENTS ===============================
# ============================================================

def _ensure_defaults(lay: Dict[str, Any]):
    lay.setdefault("canvas", {"width": 1400, "height": 3000})
    lay.setdefault("grid", {"cols": 12, "rows": 12, "gutter": 16})

    if "components" not in lay:
        lay["components"] = []

    ids = {c["id"] for c in lay["components"]}

    def def_area(a, b, c, d):
        return {"col_start": a, "col_end": b, "row_start": c, "row_end": d}

    if "title" not in ids:
        lay["components"].append({
            "id": "title",
            "type": "text",
            "content": "",
            "area": def_area(1, 12, 1, 2),
            "align": "center"
        })

    if "primary_image" not in ids:
        lay["components"].append({
            "id": "primary_image",
            "type": "image",
            "src": "",
            "area": def_area(4, 9, 3, 8),
            "fit": "contain"
        })

    if "description" not in ids:
        lay["components"].append({
            "id": "description",
            "type": "text",
            "content": "",
            "area": def_area(2, 11, 9, 11),
            "align": "left"
        })

    if "logo" not in ids:
        lay["components"].append({
            "id": "logo",
            "type": "image",
            "src": "",
            "area": def_area(11, 12, 1, 2),
            "fit": "contain"
        })


# ============================================================
# ========== SMART OVERLAY INFERENCE ==========================
# ============================================================

def infer_overlay(instr: str) -> dict:
    s = (instr or "").lower()

    if not any(k in s for k in [
        "overlay", "pocket", "chest", "tumpuk", "center", "kantong"
    ]):
        return {}

    # position
    if any(k in s for k in ["pocket", "kantong", "chest", "center", "tengah"]):
        pos = "center"
    elif "top left" in s or "kiri atas" in s:
        pos = "top-left"
    elif "top right" in s or "kanan atas" in s:
        pos = "top-right"
    elif "bottom left" in s or "kiri bawah" in s:
        pos = "bottom-left"
    else:
        pos = "bottom-right"

    # extract size ratio
    size = 0.18
    m = re.search(r"(?:size|ratio)\s*[:= ]\s*(0\.\d+)", s)
    if m:
        try:
            size = float(m.group(1))
        except:
            pass

    return {
        "overlay": True,
        "pos": pos,
        "size_ratio": size,
        "target": "primary_image",
    }


# ============================================================
# ========== run_crewai() — CLEAN, ANTI-DOUBLE JSON ===========
# ============================================================

def run_crewai(product_prompt: str, layout_instruction: str, logo_hint: str = "") -> Dict[str, Any]:
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.4,
        model_kwargs={"response_format": {"type": "json_object"}}
    )

    # ------------------------- AGENTS -------------------------

    factual = Agent(
        role="Factual Agent",
        goal="Create short, clear ad copy.",
        backstory="Writes clean titles / taglines.",
        allow_delegation=False,
        llm=llm,
    )

    visual = Agent(
        role="Visual Agent",
        goal="Suggest product and background image queries.",
        backstory="Image query specialist.",
        allow_delegation=False,
        llm=llm,
    )

    layout = Agent(
        role="Layout Agent",
        goal="Return EXACT JSON layout schema.",
        backstory="Grid layout master.",
        allow_delegation=False,
        llm=llm,
    )

    smart = Agent(
        role="Smart Overlay Agent",
        goal="Improve logo placement (pocket/chest/center).",
        backstory="Understands fashion placement.",
        allow_delegation=False,
        llm=llm,
    )

    # -------------------------- TASKS -------------------------

    t_f = Task(
        description=f"""
Return ONLY JSON:
{{"title": "", "subtitle": "", "taglines": ["", "", ""]}}
Use product prompt:
{product_prompt}
""",
        expected_output="Valid JSON object only.",
        agent=factual
    )

    t_v = Task(
        description=f"""
Based on product prompt:
{product_prompt}

Return ONLY:
{{"product_query": "", "background_query": ""}}
""",
        expected_output="Valid JSON object only.",
        agent=visual
    )

    # STRICT LAYOUT (Patch utama anti double JSON)
    t_l = Task(
        description="""
Return EXACTLY this JSON structure (NO wrappers):

{
  "canvas": {"width": 1400, "height": 3000},
  "grid": {"cols": 12, "rows": 12, "gutter": 16},
  "components": [
      {
        "id": "title",
        "type": "text",
        "content": "",
        "area": {"col_start": 1, "col_end": 12, "row_start": 1, "row_end": 2},
        "align": "center"
      },
      {
        "id": "primary_image",
        "type": "image",
        "src": "",
        "area": {"col_start": 4, "col_end": 9, "row_start": 3, "row_end": 8},
        "fit": "contain"
      },
      {
        "id": "description",
        "type": "text",
        "content": "",
        "area": {"col_start": 2, "col_end": 11, "row_start": 9, "row_end": 11},
        "align": "left"
      },
      {
        "id": "logo",
        "type": "image",
        "src": "",
        "area": {"col_start": 11, "col_end": 12, "row_start": 1, "row_end": 2},
        "fit": "contain"
      }
  ]
}

RULES:
- do NOT output "layout":{}
- do NOT wrap JSON
- ONLY adjust numeric grid areas if needed.
- absolutely NO prose.
""",
        expected_output="Valid JSON object only.",
        agent=layout
    )

    t_s = Task(
        description=f"""
Refine this layout JSON by adjusting ONLY logo overlay properties.
Instruction:
{layout_instruction}

Rules:
- Modify only logo overlay fields.
- Do NOT change canvas/grid structure.
- Return ONE JSON object only.
""",
        expected_output="Valid JSON object only.",
        agent=smart
    )

    # -------------------------- RUN --------------------------

    crew = Crew(
        agents=[factual, visual, layout, smart],
        tasks=[t_f, t_v, t_l, t_s],
        process=Process.sequential,
    )
    results = crew.kickoff()

    # ------------------------- PARSE OUTPUTS -------------------------

    fact = to_json(t_f.output.raw)
    vis  = to_json(t_v.output.raw)
    lay  = to_json(t_l.output.raw)

    _ensure_defaults(lay)

    # inject data from factual
    for c in lay["components"]:
        if c["id"] == "title":
            c["content"] = fact.get("title", "")

        elif c["id"] == "description":
            c["content"] = fact.get("subtitle", "")

    # fetch product image
    prod_urls = _unsplash_search(vis.get("product_query", ""), 1)

    for c in lay["components"]:
        if c["id"] == "primary_image" and not c.get("src"):
            c["src"] = prod_urls[0] if prod_urls else ""

        if c["id"] == "logo" and not c.get("src") and logo_hint:
            c["src"] = logo_hint

    # ------------------------------------------------------------
    # OVERLAY LOGIC FIX (PRIORITAS: USER PROMPT > SMART AGENT)
    # ------------------------------------------------------------

    # Step 1: user instruction punya prioritas tertinggi
    user_ov = infer_overlay(layout_instruction)

    if user_ov:
        for c in lay["components"]:
            if c["id"] == "logo":
                c["overlay_target"] = user_ov["target"]
                c["overlay_pos"] = user_ov["pos"]
                c["overlay_size_ratio"] = user_ov["size_ratio"]
                c.setdefault("overlay_offset", [0, 0])

    else:
        # Step 2: kalau user tidak spesifik, baru panggil Smart Overlay
        t_s.description = f"""
Refine logo placement only (no structure changes).
Return ONE JSON object:
{json.dumps(lay)}
"""
        crew2 = Crew(agents=[smart], tasks=[t_s], process=Process.sequential)
        res2 = crew2.kickoff()  # noqa: F841 (result tidak dipakai langsung)

        lay2 = to_json(t_s.output.raw) or lay
        _ensure_defaults(lay2)
        lay = lay2

    # ------------------------------------------------------------
    # FINAL OUTPUT
    # ------------------------------------------------------------
    return {
        "factual": fact,
        "visual": vis,
        "graph": lay
    }

# ============================================================
# ========== STREAMLIT UI ====================================
# ============================================================

st.title("🧠 CrewAI Poster — Smart Overlay Designer")

if "outputs" not in st.session_state:
    st.session_state.outputs = None
if "poster_img" not in st.session_state:
    st.session_state.poster_img = None
if "stage" not in st.session_state:
    st.session_state.stage = "input"


# Sidebar polish controls
st.sidebar.header("🎛️ Polish Controls")
POLISH_CONTRAST = st.sidebar.slider("Contrast", 0.85, 1.35, 1.05, 0.01)
POLISH_GRAIN    = st.sidebar.slider("Paper Texture", 0, 40, 18, 1)
POLISH_LIGHT    = st.sidebar.slider("Light Boost", 0.7, 1.5, 1.0, 0.05)


# ============================================================
# ========== STEP 1 — INPUT ==================================
# ============================================================

with st.expander("Step 1 — Input", expanded=st.session_state.stage == "input"):
    product_prompt = st.text_area(
        "Product prompt:",
        "Create an ad for women's cotton shirt (S-XL), pastel color palette."
    )
    layout_instruction = st.text_input(
        "Layout instruction:",
        "Image on right. Title centered. Overlay the logo on chest pocket."
    )
    logo_src = st.text_input(
        "Logo URL or local path:",
        ""
    )

    if st.button("Run with CrewAI", type="primary"):
        if not OPENAI_API_KEY:
            st.error("OPENAI_API_KEY missing in .env")
            st.stop()

        try:
            outs = run_crewai(product_prompt, layout_instruction, logo_hint=logo_src)
            st.session_state.outputs = outs

            img = render_from_grid(outs["graph"], save_path="dataset/poster.png")
            st.session_state.poster_img = img

            st.session_state.stage = "review"
            st.rerun()
        except Exception as e:
            st.exception(e)


# ============================================================
# ========== STEP 2 — REVIEW =================================
# ============================================================

if st.session_state.stage == "review":
    outs = st.session_state.outputs
    poster = st.session_state.poster_img

    st.subheader("Generated Poster")
    st.image(poster, use_column_width=True)

    c1, c2 = st.columns([1.4, 1])
    with c1:
        st.markdown("### Layout JSON")
        st.json(outs["graph"])

    with c2:
        if st.button("✨ Polish Poster"):
            try:
                polished = render_polished(
                    outs["graph"], outs["factual"],
                    contrast=POLISH_CONTRAST,
                    grain=POLISH_GRAIN,
                    light=POLISH_LIGHT,
                    save_path="dataset/poster_polished.png"
                )
                st.session_state.poster_img = polished
                st.success("Polished!")
            except Exception as e:
                st.exception(e)

        buf = BytesIO()
        st.session_state.poster_img.save(buf, format="PNG")
        st.download_button(
            "⬇️ Download poster.png",
            data=buf.getvalue(),
            file_name="poster.png",
            mime="image/png"
        )

        st.download_button(
            "⬇️ Download layout.json",
            data=json.dumps(outs["graph"], indent=2),
            file_name="layout.json",
            mime="application/json"
        )

    # ------------------------------------------------------------
    st.markdown("---")
    st.header("🎨 Step 2.5 — Design Mode (Drag & Drop)")
    # ------------------------------------------------------------

    bg = st.session_state.poster_img.copy()
    bg_w, bg_h = bg.size

    scale = st.slider("Preview scale", 0.3, 1.0, 0.55, 0.05)
    pv_w, pv_h = int(bg_w * scale), int(bg_h * scale)
    bg_preview = bg.resize((pv_w, pv_h), Image.LANCZOS)

    def to_pv(v): return int(v * scale)
    def from_pv(v): return int(v / scale)

    g = outs["graph"]
    comps = {c["id"]: c for c in g["components"]}

    W, H = g["canvas"]["width"], g["canvas"]["height"]
    cols, rows, gg = g["grid"]["cols"], g["grid"]["rows"], g["grid"]["gutter"]
    cw = (W - (cols - 1) * gg) / cols
    ch = (H - (rows - 1) * gg) / rows

    def comp_box(cid):
        a = comps[cid]["area"]
        return (
            int((a["col_start"] - 1) * (cw + gg)),
            int((a["row_start"] - 1) * (ch + gg)),
            int(a["col_end"] * cw + (a["col_end"] - 1) * gg),
            int(a["row_end"] * ch + (a["row_end"] - 1) * gg),
        )

    # ------------- Build Canvas Objects (NO TITLE TEXT) -----------

    canvas_objects = []

        # ---- Phantom TITLE bounding box (draggable, no text drawn) ----
    if "title" in comps:
        x0, y0, x1, y1 = comp_box("title")
        canvas_objects.append({
            "type": "rect",
            "left": to_pv(x0),
            "top": to_pv(y0),
            "width": to_pv(x1 - x0),
            "height": to_pv(y1 - y0),
            "fill": "rgba(0,0,0,0)",
            "stroke": "#FF00AA",
            "strokeWidth": 2
        })


    # primary image frame
    if "primary_image" in comps:
        x0, y0, x1, y1 = comp_box("primary_image")
        canvas_objects.append({
            "type": "rect",
            "left": to_pv(x0),
            "top": to_pv(y0),
            "width": to_pv(x1 - x0),
            "height": to_pv(y1 - y0),
            "fill": "rgba(0,0,0,0)",
            "stroke": "#2E86DE",
            "strokeWidth": 2
        })

    # ---- Phantom LOGO frame (always draggable) ----
    if "logo" in comps:
    # if overlay: compute phantom box based on primary image
        if "overlay_target" in comps["logo"]:
            px0, py0, px1, py1 = comp_box("primary_image")

        # approximate overlay box by size ratio
        ratio = comps["logo"].get("overlay_size_ratio", 0.18)
        ow = int((px1 - px0) * ratio)
        oh = ow

        # center based on offset
        off = comps["logo"].get("overlay_offset", [0, 0])
        cx = (px0 + px1)//2 + off[0]
        cy = (py0 + py1)//2 + off[1]

        x0 = cx - ow//2
        y0 = cy - oh//2
        x1 = x0 + ow
        y1 = y0 + oh

    else:
        # normal grid mode
        x0, y0, x1, y1 = comp_box("logo")

    canvas_objects.append({
        "type": "rect",
        "left": to_pv(x0),
        "top": to_pv(y0),
        "width": to_pv(x1 - x0),
        "height": to_pv(y1 - y0),
        "fill": "rgba(0,0,0,0)",
        "stroke": "#27AE60",
        "strokeWidth": 2
    })


    # ------------- Canvas UI --------------------------------------

    canvas_res = st_canvas(
        background_image=bg_preview,
        height=pv_h,
        width=pv_w,
        drawing_mode="transform",
        initial_drawing={"version": "5.2.4", "objects": canvas_objects},
        update_streamlit=True,
        key="designer"
    )

    st.caption("Geser frame biru (produk) & hijau (logo).")

    # ------------- Grid conversion --------------------------------

    def xy_to_area(left, top, width, height):
        col_start = max(1, int(from_pv(left) / (cw + gg)) + 1)
        row_start = max(1, int(from_pv(top) / (ch + gg)) + 1)
        col_end = min(cols, int(from_pv(left + width) / (cw + gg)) + 1)
        row_end = min(rows, int(from_pv(top + height) / (ch + gg)) + 1)

        if col_end <= col_start:
            col_end = min(cols, col_start + 1)
        if row_end <= row_start:
            row_end = min(rows, row_start + 1)

        return {
            "col_start": col_start,
            "col_end": col_end,
            "row_start": row_start,
            "row_end": row_end,
        }

    # ---------------- Apply canvas changes ------------------------

st.subheader("Apply changes")

attach = st.checkbox("Attach LOGO to product (chest overlay)", value=True)

if st.button("✅ Apply from Canvas"):
    try:
        objs = (canvas_res.json_data or {}).get("objects", [])

        # ----- TITLE (phantom box index 0) -----
        if len(objs) >= 1 and "title" in comps:
            comps["title"]["area"] = xy_to_area(
                objs[0]["left"], objs[0]["top"],
                objs[0]["width"], objs[0]["height"]
            )

        # ----- PRIMARY IMAGE (index 1 when title exists) -----
        if len(objs) >= 2 and "primary_image" in comps:
            comps["primary_image"]["area"] = xy_to_area(
                objs[1]["left"], objs[1]["top"],
                objs[1]["width"], objs[1]["height"]
            )

        # ----- LOGO (index depends on overlay or not) -----
        if len(objs) >= 3 and "logo" in comps:
            lo = objs[2]

            if attach:
                px0, py0, px1, py1 = comp_box("primary_image")
                cx = from_pv(lo["left"] + lo["width"] / 2)
                cy = from_pv(lo["top"]  + lo["height"] / 2)

                comps["logo"]["overlay_target"] = "primary_image"
                comps["logo"]["overlay_pos"] = "center"
                comps["logo"]["overlay_size_ratio"] = min(
                    0.6,
                    max(0.05, from_pv(lo["width"]) / max(1, (px1 - px0)))
                )
                comps["logo"]["overlay_offset"] = [
                    int(cx - (px0 + px1) / 2),
                    int(cy - (py0 + py1) / 2),
                ]

            else:
                comps["logo"]["area"] = xy_to_area(
                    lo["left"], lo["top"], lo["width"], lo["height"]
                )
                for k in ["overlay_target", "overlay_pos", "overlay_size_ratio",
                          "overlay_offset", "overlay_stack"]:
                    comps["logo"].pop(k, None)

        # commit back
        g["components"] = list(comps.values())
        img = render_from_grid(g, save_path="dataset/poster.png")
        st.session_state.poster_img = img

        st.success("Applied!")
        st.rerun()

    except Exception as e:
        st.exception(e)

    # ---------------- Re-sync with AI -----------------------------

    if st.button("🤝 Re-sync with AI (Smart Overlay refine)"):
        try:
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.2,
                model_kwargs={"response_format": {"type": "json_object"}}
            )
            prompt = (
                "Refine ONLY logo placement. Return ONE JSON object:\n" +
                json.dumps(st.session_state.outputs["graph"])
            )
            out = llm.invoke(prompt).content

            new = to_json(out) or st.session_state.outputs["graph"]
            st.session_state.outputs["graph"] = new

            img = render_from_grid(new, save_path="dataset/poster.png")
            st.session_state.poster_img = img

            st.success("Re-synced!")
            st.rerun()

        except Exception as e:
            st.exception(e)

    # ---------------- Re-run CrewAI ------------------------------

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        revised_prompt = st.text_area("Revised product prompt:", "")
        revised_layout = st.text_input("Revised layout instruction:", "")
        revised_logo   = st.text_input("Revised logo URL/path:", "")

        if st.button("🔁 Re-run CrewAI"):
            try:
                outs = run_crewai(
                    revised_prompt.strip() or product_prompt,
                    revised_layout.strip() or layout_instruction,
                    logo_hint=(revised_logo.strip() or logo_src)
                )
                st.session_state.outputs = outs
                st.session_state.poster_img = render_from_grid(
                    outs["graph"], save_path="dataset/poster.png"
                )
                st.success("Regenerated with CrewAI.")
                st.rerun()
            except Exception as e:
                st.exception(e)

    with col2:
        if st.button("↩️ Back to Step 1"):
            st.session_state.stage = "input"
            st.rerun()
