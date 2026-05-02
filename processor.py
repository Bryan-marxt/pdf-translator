import base64
import io
import json
import os
import re
import time
from pathlib import Path

import fitz  # PyMuPDF
import PIL.Image
from google import genai
from google.genai import types as gtypes
from PIL import ImageDraw, ImageFont
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------

def _register_fonts() -> bool:
    candidates = [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular):
            try:
                pdfmetrics.registerFont(TTFont("MainFont", regular))
                pdfmetrics.registerFont(TTFont("MainFont-Bold", bold if os.path.exists(bold) else regular))
                return True
            except Exception:
                continue
    return False


_FONT_OK = _register_fonts()
BODY_FONT = "MainFont" if _FONT_OK else "Helvetica"
BOLD_FONT = "MainFont-Bold" if _FONT_OK else "Helvetica-Bold"
ARIAL_PATH = "C:/Windows/Fonts/arial.ttf"


# ---------------------------------------------------------------------------
# ReportLab markup helpers
# ---------------------------------------------------------------------------

def _to_markup(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = text.split("**")
    if len(parts) % 2 == 1 and len(parts) > 1:
        text = "".join(f"<b>{p}</b>" if i % 2 == 1 else p for i, p in enumerate(parts))
    parts = text.split("_")
    if len(parts) % 2 == 1 and len(parts) > 1:
        text = "".join(f"<i>{p}</i>" if i % 2 == 1 else p for i, p in enumerate(parts))
    return text


def _safe_text(text: str) -> str:
    if BODY_FONT == "Helvetica":
        return text.encode("latin-1", errors="replace").decode("latin-1")
    return text


def _make_paragraph(content: str, style) -> Paragraph:
    try:
        return Paragraph(content, style)
    except Exception:
        plain = re.sub(r"<[^>]+>", "", content)
        try:
            return Paragraph(plain, style)
        except Exception:
            return Paragraph("", style)


# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------

_FAKE_FOOTNOTE_PATTERNS = [
    r"\d+\.\s*\[Nota de rodapé não incluída[^\]\n]*\]",
    r"\d+\.\s*\[Footnote not included[^\]\n]*\]",
    r"\d+\.\s*\[footnote[^\]\n]*\]",
]


def clean_fake_footnotes(text: str) -> str:
    for pattern in _FAKE_FOOTNOTE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\nNotas de rodapé:\s*(\n\s*)*(\n|$)", "\n", text)
    return text.strip()


def stitch_boundaries(pages: list[str]) -> list[str]:
    SENTENCE_ENDERS = set('.!?:;"\'»—')
    CONTINUATION_STARTERS = {
        "e", "ou", "mas", "que", "de", "do", "da", "dos", "das", "para",
        "com", "por", "em", "se", "o", "a", "os", "as", "um", "uma",
        "no", "na", "nos", "nas", "ao", "à", "aos", "às",
        "and", "or", "but", "the", "a", "an", "to", "of", "in", "on",
        "at", "by", "with", "from", "that", "which", "who", "is", "are",
        "was", "were", "has", "have", "had", "it", "its",
    }
    pages = list(pages)
    for i in range(len(pages) - 1):
        lines_i    = pages[i].rstrip("\n").split("\n")
        lines_next = pages[i + 1].lstrip("\n").split("\n")
        if not lines_i or not lines_next:
            continue
        last  = lines_i[-1].strip()
        first = lines_next[0].strip()
        if not last or not first:
            continue
        if last.startswith(("#", "[IMAGEM", "-", "*", "•")) or first.startswith(("#", "[IMAGEM")):
            continue
        ends_incomplete  = last[-1] not in SENTENCE_ENDERS
        first_word       = first.split()[0].rstrip(".,;").lower() if first.split() else ""
        starts_lower     = first[0].islower()
        starts_connector = first_word in CONTINUATION_STARTERS
        if ends_incomplete and (starts_lower or starts_connector):
            lines_i[-1]  = last + " " + first
            pages[i]     = "\n".join(lines_i)
            pages[i + 1] = "\n".join(lines_next[1:])
    return pages


# ---------------------------------------------------------------------------
# PIL image overlay (fallback for image translation)
# ---------------------------------------------------------------------------

def _pil_font(size_px: int):
    try:
        return ImageFont.truetype(ARIAL_PATH, size_px)
    except Exception:
        return ImageFont.load_default()


def _draw_translated_text(image: PIL.Image.Image, items: list[dict]) -> PIL.Image.Image:
    img  = image.copy().convert("RGB")
    draw = ImageDraw.Draw(img)
    size_factor = {"small": 0.018, "medium": 0.028, "large": 0.042}
    for item in items:
        x   = int(item["x"] * img.width)
        y   = int(item["y"] * img.height)
        w   = int(item["w"] * img.width)
        h   = int(item["h"] * img.height)
        translated = item.get("translated", "").strip()
        if not translated:
            continue
        font_px = max(9, int(img.height * size_factor.get(item.get("size", "medium"), 0.028)))
        font    = _pil_font(font_px)
        pad     = 3
        draw.rectangle([x - pad, y - pad, x + w + pad, y + h + pad], fill="white")
        draw.text((x + w // 2, y + h // 2), translated, fill="black", font=font, anchor="mm")
    return img


# ---------------------------------------------------------------------------
# Main processor class
# ---------------------------------------------------------------------------

class PDFProcessor:
    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-3.1-flash-lite-preview",
        cache_dir: Path | None = None,
    ):
        self._api_key    = api_key
        self._model_name = model_name
        self._client     = genai.Client(api_key=api_key)
        self.cache_dir   = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_path(self, page_num: int) -> Path | None:
        return self.cache_dir / f"pagina_{page_num:04d}.txt" if self.cache_dir else None

    def _img_cache_path(self, page_num: int, img_idx: int, kind: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"pagina_{page_num:04d}_img{img_idx:02d}_{kind}.png"

    def load_cached_page(self, page_num: int) -> str | None:
        p = self._cache_path(page_num)
        return p.read_text(encoding="utf-8") if p and p.exists() else None

    def save_cached_page(self, page_num: int, text: str) -> None:
        p = self._cache_path(page_num)
        if p:
            p.write_text(text, encoding="utf-8")

    def load_cached_image(self, page_num: int, img_idx: int, kind: str) -> PIL.Image.Image | None:
        p = self._img_cache_path(page_num, img_idx, kind)
        if p and p.exists():
            return PIL.Image.open(p).copy()
        return None

    def save_cached_image(self, page_num: int, img_idx: int, kind: str, img: PIL.Image.Image) -> None:
        p = self._img_cache_path(page_num, img_idx, kind)
        if p:
            img.save(str(p), format="PNG")

    def cached_pages(self) -> set[int]:
        if not self.cache_dir or not self.cache_dir.exists():
            return set()
        result = set()
        for f in self.cache_dir.glob("pagina_????.txt"):
            try:
                result.add(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return result

    # ------------------------------------------------------------------
    # Step 1a — PDF → page renders for Gemini
    # ------------------------------------------------------------------

    def pdf_to_images(self, pdf_path: str) -> list[PIL.Image.Image]:
        doc    = fitz.open(pdf_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            images.append(PIL.Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
        doc.close()
        return images

    # ------------------------------------------------------------------
    # Step 1b — Extract embedded images from native PDF pages
    # ------------------------------------------------------------------

    def extract_native_images(self, pdf_path: str) -> dict[int, list[PIL.Image.Image]]:
        doc    = fitz.open(pdf_path)
        result: dict[int, list[PIL.Image.Image]] = {}
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            imgs: list[PIL.Image.Image] = []
            seen = set()
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    raw = doc.extract_image(xref)
                    img = PIL.Image.open(io.BytesIO(raw["image"])).convert("RGB")
                    if img.width >= 100 and img.height >= 100:
                        imgs.append(img)
                except Exception:
                    continue
            if imgs:
                result[page_idx + 1] = imgs
        doc.close()
        return result

    # ------------------------------------------------------------------
    # Step 2a — Transcribe / translate a page via Gemini
    # ------------------------------------------------------------------

    def process_page(
        self,
        image: PIL.Image.Image,
        target_language: str | None,
        page_num: int = 0,
        retries: int = 5,
    ) -> tuple[str, bool]:
        cached = self.load_cached_page(page_num)
        if cached is not None:
            return cached, True

        lang_instruction = (
            f"Traduza todo o conteúdo textual para {target_language}."
            if target_language
            else "Mantenha o idioma original."
        )

        prompt = f"""Você é um transcritor de documentos de alta precisão com OCR avançado.

Analise esta imagem de página e transcreva TODO o conteúdo visível.
{lang_instruction}

═══ FORMATAÇÃO ═══
# Título principal
## Subtítulo de seção
### Título menor
> Texto centralizado
**negrito**
_itálico_
- item de lista
1. item numerado

═══ IMAGENS E DESENHOS ═══
Se a página contém ilustrações, diagramas ou desenhos, coloque [IMAGEM_1], [IMAGEM_2] etc. na posição exata onde aparecem no fluxo do texto.

═══ NOTAS DE RODAPÉ ═══
- Transcreva apenas notas CLARAMENTE VISÍVEIS
- NUNCA invente notas fictícias nem escreva "[Nota de rodapé não incluída]"
- Se não houver notas legíveis, não inclua a seção

═══ REGRAS ═══
- Preserve 100% do conteúdo
- Se a página não tiver texto: [Página sem texto]
- Retorne APENAS o texto formatado"""

        img_buf = io.BytesIO()
        image.save(img_buf, format="JPEG", quality=90)
        img_bytes  = img_buf.getvalue()
        image_part = gtypes.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")

        last_error: Exception = RuntimeError("Nenhuma tentativa realizada")
        for attempt in range(retries):
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=[prompt, image_part],
                    config=gtypes.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=8192,
                    ),
                )
                if response.text:
                    text = clean_fake_footnotes(response.text)
                    self.save_cached_page(page_num, text)
                    return text, False
                raise ValueError("Resposta vazia")
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)

        raise last_error

    # ------------------------------------------------------------------
    # Step 2b — Translate image text using Gemini image generation model
    # ------------------------------------------------------------------

    def translate_image_native(
        self,
        image: PIL.Image.Image,
        target_language: str,
        image_model: str = "gemini-3.1-flash-image-preview",
        page_num: int = 0,
        img_idx: int = 0,
    ) -> PIL.Image.Image | None:
        cached = self.load_cached_image(page_num, img_idx, "translated")
        if cached is not None:
            return cached

        img_buf   = io.BytesIO()
        image.save(img_buf, format="PNG")
        img_bytes = img_buf.getvalue()

        prompt = (
            f"Translate ALL text visible in this illustration or diagram to {target_language}. "
            f"Keep the exact same visual style, layout, drawings, arrows, boxes and design. "
            f"Only change the language of the text — do not alter anything else. "
            f"If the text is already in {target_language} or there is no text, return the image unchanged."
        )

        try:
            response = self._client.models.generate_content(
                model=image_model,
                contents=[
                    gtypes.Content(
                        role="user",
                        parts=[
                            gtypes.Part.from_text(text=prompt),
                            gtypes.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                        ],
                    )
                ],
                config=gtypes.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"]
                ),
            )
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    raw = part.inline_data.data
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    result = PIL.Image.open(io.BytesIO(raw)).convert("RGB")
                    self.save_cached_image(page_num, img_idx, "translated", result)
                    return result
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Step 3 — Build output PDF
    # ------------------------------------------------------------------

    def create_pdf(
        self,
        pages_text: list[str],
        page_images: dict[int, list[tuple[PIL.Image.Image, PIL.Image.Image | None]]] | None = None,
    ) -> bytes:
        page_images = page_images or {}
        buffer      = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=2.5*cm, rightMargin=2.5*cm,
            topMargin=2.5*cm,  bottomMargin=2.5*cm,
            title="Documento Transcrito",
        )

        body = ParagraphStyle("Body", fontName=BODY_FONT, fontSize=11, leading=17,
                              alignment=TA_JUSTIFY, spaceAfter=6)
        h1   = ParagraphStyle("H1",   fontName=BOLD_FONT, fontSize=16, leading=22,
                              spaceBefore=16, spaceAfter=8,  alignment=TA_CENTER)
        h2   = ParagraphStyle("H2",   fontName=BOLD_FONT, fontSize=13, leading=18,
                              spaceBefore=12, spaceAfter=6,  alignment=TA_LEFT)
        h3   = ParagraphStyle("H3",   fontName=BOLD_FONT, fontSize=11, leading=16,
                              spaceBefore=8,  spaceAfter=4,  alignment=TA_LEFT)
        ctr  = ParagraphStyle("Ctr",  fontName=BODY_FONT, fontSize=11, leading=17,
                              alignment=TA_CENTER, spaceAfter=6, spaceBefore=4)
        lst  = ParagraphStyle("List", fontName=BODY_FONT, fontSize=11, leading=16,
                              leftIndent=18, spaceAfter=3)
        cap  = ParagraphStyle("Cap",  fontName=BODY_FONT, fontSize=9,  leading=12,
                              alignment=TA_CENTER, spaceAfter=8)

        # Conservative image dimensions to avoid "too large" errors
        max_img_w = A4[0] - 6.0 * cm
        max_img_h = A4[1] - 8.0 * cm

        def _insert_image(img: PIL.Image.Image, label: str = "") -> list:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            scale  = min(max_img_w / img.width, max_img_h / img.height)
            draw_w = img.width  * scale
            draw_h = img.height * scale
            elements = [Spacer(1, 8), RLImage(buf, width=draw_w, height=draw_h)]
            if label:
                elements.append(_make_paragraph(label, cap))
            elements.append(Spacer(1, 8))
            return elements

        story = []

        for page_idx, text in enumerate(pages_text):
            if page_idx > 0:
                story.append(PageBreak())

            page_num  = page_idx + 1
            page_imgs = page_images.get(page_num, [])

            for raw_line in text.split("\n"):
                stripped = raw_line.strip()
                if not stripped:
                    story.append(Spacer(1, 5))
                    continue

                # Image marker
                img_match = re.match(r"^\[IMAGEM_(\d+)\]$", stripped, re.IGNORECASE)
                if img_match:
                    idx = int(img_match.group(1)) - 1
                    if idx < len(page_imgs):
                        orig, trans = page_imgs[idx]
                        if trans is not None:
                            story += _insert_image(trans, "Versão traduzida")
                            small_w = max_img_w * 0.45
                            small_h = orig.height * (small_w / orig.width)
                            buf2 = io.BytesIO()
                            orig.save(buf2, format="PNG")
                            buf2.seek(0)
                            story += [RLImage(buf2, width=small_w, height=small_h),
                                      _make_paragraph("Original", cap),
                                      Spacer(1, 8)]
                        else:
                            story += _insert_image(orig)
                    continue

                # Headings
                if stripped.startswith("# "):
                    story.append(_make_paragraph(_to_markup(_safe_text(stripped[2:])), h1))
                elif stripped.startswith("## "):
                    story.append(_make_paragraph(_to_markup(_safe_text(stripped[3:])), h2))
                elif stripped.startswith("### "):
                    story.append(_make_paragraph(_to_markup(_safe_text(stripped[4:])), h3))
                elif stripped.startswith("> "):
                    story.append(_make_paragraph(_to_markup(_safe_text(stripped[2:])), ctr))
                elif re.match(r"^[-*•]\s", stripped):
                    story.append(_make_paragraph("• " + _to_markup(_safe_text(stripped[2:])), lst))
                elif re.match(r"^\d+\.\s", stripped):
                    story.append(_make_paragraph(_to_markup(_safe_text(stripped)), lst))
                else:
                    story.append(_make_paragraph(_to_markup(_safe_text(stripped)), body))

        doc.build(story)
        return buffer.getvalue()
