import io
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tempfile as _tempfile

import fitz  # PyMuPDF
import PIL.Image
import streamlit as st
from dotenv import load_dotenv

from processor import PDFProcessor

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="PDF Transcriber & Translator",
    page_icon="📄",
    layout="wide",
)

st.title("📄 PDF Transcriber & Translator")

tab_transcribe, tab_remove = st.tabs(["🌐 Transcrever / Traduzir", "✂️ Remover Páginas"])

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Local: save cache on Desktop. Cloud (no Desktop): use system temp dir.
_desktop = Path.home() / "Desktop"
DESKTOP = _desktop if _desktop.exists() else Path(_tempfile.gettempdir())

EMPTY_PATTERNS = [
    "[página sem texto]",
    "notas de rodapé:",
    "[erro ao processar",
]


def get_cache_dir(filename: str, filesize: int, language: str | None) -> Path:
    lang_tag = language.split()[0].lower() if language else "transcricao"
    safe_name = "".join(c if c.isalnum() else "_" for c in Path(filename).stem)
    return DESKTOP / "pdf_cache" / f"{safe_name}_{filesize}_{lang_tag}"


def _is_native_empty(text: str) -> bool:
    return len(text.strip()) < 15


def _is_cache_empty(page_num: int, filename: str, filesize: int) -> bool:
    safe_name = "".join(c if c.isalnum() else "_" for c in Path(filename).stem)
    cache_base = DESKTOP / "pdf_cache"
    if not cache_base.exists():
        return False
    for cache_dir in cache_base.glob(f"{safe_name}_{filesize}_*"):
        cf = cache_dir / f"pagina_{page_num:04d}.txt"
        if cf.exists():
            content = cf.read_text(encoding="utf-8").strip().lower()
            if any(p in content for p in EMPTY_PATTERNS):
                return True
    return False


# ===========================================================================
# TAB 1 — Transcrever / Traduzir
# ===========================================================================

with tab_transcribe:
    with st.sidebar:
        st.header("⚙️ Configuração")

        api_key = st.text_input(
            "Chave da API Gemini",
            type="password",
            value=os.getenv("GEMINI_API_KEY", ""),
            help="Obtenha gratuitamente em aistudio.google.com/apikey",
        )
        if not api_key:
            st.warning("Insira sua chave da API Gemini para continuar.")

        st.markdown("---")

        model_name = st.text_input(
            "Modelo Gemini",
            value="gemini-3.1-flash-lite-preview",
            help="Modelos: gemini-3.1-flash-lite-preview · gemini-2.0-flash · gemini-2.0-flash-lite",
        )

        st.markdown("---")

        translate = st.checkbox("Traduzir para outro idioma", value=True)

        LANGUAGES = [
            "Português", "English", "Español", "Français", "Deutsch",
            "Italiano", "日本語 (Japonês)", "中文 (Chinês)", "Русский (Russo)", "العربية (Árabe)",
        ]
        target_language: str | None = None
        if translate:
            target_language = st.selectbox("Idioma de destino", LANGUAGES, index=0)

        st.markdown("---")

        concurrency = st.slider(
            "Páginas simultâneas",
            min_value=1, max_value=10, value=5,
            help="Quantas páginas enviar ao Gemini ao mesmo tempo.",
        )

        st.markdown("---")
        translate_images = st.checkbox(
            "🎨 Traduzir imagens/desenhos",
            value=False,
            help="Usa o modelo de geração de imagens do Gemini para traduzir texto dentro de ilustrações e diagramas.",
        )
        if translate_images:
            image_model = st.selectbox(
                "Modelo de imagem",
                ["gemini-3.1-flash-image-preview", "gemini-3.1-pro-image-preview"],
                help="Flash: ~US$0,07/imagem · Pro: ~US$0,13/imagem (mais fiel ao original)",
            )
            st.warning("⚠️ Custo extra: ~US$0,07 por imagem com Flash")
        else:
            image_model = "gemini-3.1-flash-image-preview"

        st.markdown("---")
        st.markdown("**Custo estimado (texto):** ~US$ 0,001/página  \n200 páginas ≈ US$ 0,20")

    # -----------------------------------------------------------------------
    # Upload + cache status
    # -----------------------------------------------------------------------

    st.markdown("### Enviar PDF")
    uploaded_file = st.file_uploader(
        "Arraste o PDF aqui ou clique para selecionar",
        type="pdf",
        help="Funciona com PDFs escaneados e PDFs com texto nativo.",
        key="transcribe_uploader",
    )

    force_reprocess = False

    if uploaded_file:
        size_kb = uploaded_file.size / 1024
        st.info(f"**Arquivo:** {uploaded_file.name} — {size_kb:.1f} KB")

        cache_dir = get_cache_dir(uploaded_file.name, uploaded_file.size, target_language)
        if cache_dir.exists():
            cached_count = len(list(cache_dir.glob("pagina_*.txt")))
            if cached_count > 0:
                col_info, col_btn = st.columns([3, 1])
                with col_info:
                    st.success(f"💾 **{cached_count} página(s) em cache** — o sistema retoma de onde parou.")
                with col_btn:
                    force_reprocess = st.checkbox(
                        "🔄 Reprocessar do zero",
                        help="Apaga o cache e processa tudo novamente.",
                    )

    can_process = bool(uploaded_file and api_key)

    if st.button("🚀 Processar PDF", disabled=not can_process, type="primary", key="btn_process"):

        cache_dir = get_cache_dir(uploaded_file.name, uploaded_file.size, target_language)

        if force_reprocess and cache_dir.exists():
            for f in cache_dir.glob("pagina_*"):
                f.unlink(missing_ok=True)
            st.toast("Cache apagado — processando do zero.", icon="🗑️")

        processor = PDFProcessor(api_key=api_key, model_name=model_name, cache_dir=cache_dir)

        pdf_bytes = uploaded_file.read()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            with st.status("Processando PDF…", expanded=True) as status_box:

                st.write("📸 Convertendo páginas em imagens…")
                try:
                    images = processor.pdf_to_images(tmp_path)
                except Exception as exc:
                    st.error(f"Erro ao ler o PDF: {exc}")
                    st.stop()

                total = len(images)
                already_cached = processor.cached_pages()
                remaining = total - len(already_cached)
                st.write(f"✅ {total} página(s). **{len(already_cached)} em cache**, {remaining} a processar.")

                progress_bar = st.progress(0)
                status_line = st.empty()
                errors: list[str] = []
                results: dict[int, str] = {}

                for i in range(total):
                    pn = i + 1
                    if pn in already_cached:
                        results[pn] = processor.load_cached_page(pn) or ""

                completed = len(results)
                progress_bar.progress(completed / total if total else 1)

                pending = [(i, images[i]) for i in range(total) if (i + 1) not in already_cached]

                if pending:
                    status_line.write(f"🔄 Processando {len(pending)} página(s) em grupos de {concurrency}…")
                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        future_to_page = {
                            executor.submit(processor.process_page, img, target_language, i + 1): i + 1
                            for i, img in pending
                        }
                        for future in as_completed(future_to_page):
                            page_num = future_to_page[future]
                            try:
                                text, _ = future.result()
                                results[page_num] = text
                            except Exception as exc:
                                msg = f"Página {page_num}: {exc}"
                                errors.append(msg)
                                results[page_num] = f"[Erro ao processar esta página: {exc}]"
                                st.warning(msg)
                            completed += 1
                            progress_bar.progress(completed / total)
                            status_line.write(f"✅ {completed}/{total} concluídas (última: pág. {page_num})")

                status_line.write(f"✅ Todas as {total} páginas processadas.")
                texts = [results.get(i + 1, "[Página não processada]") for i in range(total)]

                # ── Post-processing: stitch broken sentences at page boundaries ──
                st.write("🔗 Costurando frases partidas entre páginas…")
                from processor import stitch_boundaries, clean_fake_footnotes
                texts = stitch_boundaries(texts)
                texts = [clean_fake_footnotes(t) for t in texts]

                # ── Extract embedded images from the PDF ──────────────────────
                st.write("🖼️ Extraindo imagens embutidas do PDF…")
                native_images = processor.extract_native_images(tmp_path)
                total_imgs = sum(len(v) for v in native_images.values())
                img_pages  = len(native_images)
                if img_pages:
                    st.write(f"✅ {img_pages} página(s) com {total_imgs} imagem(ns) encontradas.")
                else:
                    st.write("ℹ️ Nenhuma imagem embutida encontrada (PDF escaneado ou sem imagens).")

                # ── Translate image text (optional, parallel) ─────────────────
                page_images: dict = {}

                if native_images and translate_images and target_language:
                    st.write(f"🎨 Traduzindo {total_imgs} imagem(ns) com {image_model}…")
                    img_progress = st.progress(0)
                    img_results: dict[tuple[int, int], PIL.Image.Image | None] = {}

                    with ThreadPoolExecutor(max_workers=3) as img_executor:
                        img_futures = {
                            img_executor.submit(
                                processor.translate_image_native,
                                img, target_language, image_model, page_num, idx
                            ): (page_num, idx, img)
                            for page_num, imgs in native_images.items()
                            for idx, img in enumerate(imgs)
                        }
                        done_count = 0
                        for future in as_completed(img_futures):
                            page_num, idx, orig = img_futures[future]
                            try:
                                img_results[(page_num, idx)] = future.result()
                            except Exception:
                                img_results[(page_num, idx)] = None
                            done_count += 1
                            img_progress.progress(done_count / total_imgs)

                    for page_num, imgs in native_images.items():
                        page_images[page_num] = [
                            (img, img_results.get((page_num, idx)))
                            for idx, img in enumerate(imgs)
                        ]
                    st.write("✅ Imagens traduzidas.")

                elif native_images:
                    # Include images without translation
                    for page_num, imgs in native_images.items():
                        page_images[page_num] = [(img, None) for img in imgs]

                # ── Generate output PDF ───────────────────────────────────────
                st.write("📄 Gerando PDF de saída…")
                try:
                    output_bytes = processor.create_pdf(texts, page_images)
                except Exception as exc:
                    st.error(f"Erro ao gerar o PDF: {exc}")
                    st.stop()

                if errors:
                    status_box.update(label=f"Concluído com {len(errors)} erro(s).", state="error", expanded=True)
                else:
                    status_box.update(label="Concluído com sucesso! ✅", state="complete")

            stem = Path(uploaded_file.name).stem
            lang_tag = target_language.split()[0].lower() if target_language else "transcrito"
            out_name = f"{stem}_{lang_tag}.pdf"

            st.success(f"PDF pronto: **{out_name}**")
            st.download_button("⬇️ Baixar PDF", data=output_bytes, file_name=out_name, mime="application/pdf", type="primary")
            st.caption(f"💾 Cache em: `{cache_dir}`")

        finally:
            os.unlink(tmp_path)


# ===========================================================================
# TAB 2 — Remover Páginas
# ===========================================================================

with tab_remove:
    st.markdown("### ✂️ Visualizar e Remover Páginas")
    st.markdown(
        "Carregue um PDF, visualize todas as páginas em grade, marque as que quer remover "
        "e gere um PDF limpo — **sem usar IA**."
    )

    remove_file = st.file_uploader(
        "Carregar PDF para editar",
        type="pdf",
        key="remove_uploader",
    )

    if remove_file:
        file_id = remove_file.file_id
        sk_thumbs = f"rm_thumbs_{file_id}"
        sk_texts  = f"rm_texts_{file_id}"
        sk_total  = f"rm_total_{file_id}"
        sk_data   = f"rm_data_{file_id}"

        # ── Load & cache thumbnails (runs only once per file) ──────────────
        if sk_thumbs not in st.session_state:
            with st.spinner(f"Carregando {remove_file.name}…"):
                raw = remove_file.read()
                doc = fitz.open(stream=raw, filetype="pdf")
                thumbs, native_texts = [], []
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.28, 0.28))
                    thumbs.append(pix.tobytes("png"))
                    native_texts.append(page.get_text().strip())
                doc.close()
                st.session_state[sk_thumbs] = thumbs
                st.session_state[sk_texts]  = native_texts
                st.session_state[sk_total]  = len(thumbs)
                st.session_state[sk_data]   = raw

        thumbs       = st.session_state[sk_thumbs]
        native_texts = st.session_state[sk_texts]
        total        = st.session_state[sk_total]

        # ── Controls bar ───────────────────────────────────────────────────
        st.markdown(f"**{total} páginas** encontradas.")

        ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([2, 2, 2, 2, 2])

        with ctrl1:
            cols_count = st.select_slider("Colunas na grade", options=[2, 3, 4, 5, 6], value=5)

        with ctrl2:
            if st.button("🤖 Marcar vazias automaticamente"):
                for i in range(total):
                    native_empty = _is_native_empty(native_texts[i])
                    cache_empty  = _is_cache_empty(i + 1, remove_file.name, remove_file.size)
                    st.session_state[f"rm_{file_id}_{i}"] = native_empty or cache_empty
                st.rerun()

        with ctrl3:
            if st.button("☑️ Marcar todas"):
                for i in range(total):
                    st.session_state[f"rm_{file_id}_{i}"] = True
                st.rerun()

        with ctrl4:
            if st.button("⬜ Desmarcar todas"):
                for i in range(total):
                    st.session_state[f"rm_{file_id}_{i}"] = False
                st.rerun()

        with ctrl5:
            pages_to_remove = [i for i in range(total) if st.session_state.get(f"rm_{file_id}_{i}", False)]
            st.metric("Marcadas para remover", len(pages_to_remove))

        st.markdown("---")

        # ── Thumbnail grid ─────────────────────────────────────────────────
        # Initialize default checkbox states (auto-detect empty on first load)
        for i in range(total):
            key = f"rm_{file_id}_{i}"
            if key not in st.session_state:
                st.session_state[key] = _is_native_empty(native_texts[i])

        for row_start in range(0, total, cols_count):
            cols = st.columns(cols_count)
            for col_idx, page_idx in enumerate(
                range(row_start, min(row_start + cols_count, total))
            ):
                key = f"rm_{file_id}_{page_idx}"
                is_marked = st.session_state.get(key, False)

                with cols[col_idx]:
                    # Red tint overlay when marked — using container border trick
                    if is_marked:
                        st.markdown(
                            "<div style='border:3px solid #e74c3c; border-radius:6px; padding:2px;'>",
                            unsafe_allow_html=True,
                        )

                    st.image(thumbs[page_idx], use_container_width=True)

                    if is_marked:
                        st.markdown("</div>", unsafe_allow_html=True)

                    # Checkbox — label shows page number + deletion status
                    label = f"🗑️ **Pág. {page_idx + 1}**" if is_marked else f"Pág. {page_idx + 1}"
                    st.checkbox(label, key=key)

        st.markdown("---")

        # ── Generate clean PDF ─────────────────────────────────────────────
        pages_to_remove = [i for i in range(total) if st.session_state.get(f"rm_{file_id}_{i}", False)]

        if pages_to_remove:
            kept = total - len(pages_to_remove)
            st.warning(f"**{len(pages_to_remove)} página(s) marcada(s)** para remover. PDF final terá **{kept} páginas**.")
        else:
            st.info("Nenhuma página marcada. Marque as páginas que deseja remover na grade acima.")

        if st.button(
            "✂️ Gerar PDF sem as páginas marcadas",
            disabled=not pages_to_remove,
            type="primary",
            key="btn_generate_clean",
        ):
            raw = st.session_state[sk_data]
            src_doc = fitz.open(stream=raw, filetype="pdf")
            new_doc  = fitz.open()
            remove_set = set(pages_to_remove)

            for i in range(total):
                if i not in remove_set:
                    new_doc.insert_pdf(src_doc, from_page=i, to_page=i)

            out_buf = io.BytesIO()
            new_doc.save(out_buf)
            src_doc.close()
            new_doc.close()

            out_name = f"{Path(remove_file.name).stem}_editado.pdf"
            st.success(f"✅ PDF gerado com {total - len(pages_to_remove)} páginas!")
            st.download_button(
                "⬇️ Baixar PDF editado",
                data=out_buf.getvalue(),
                file_name=out_name,
                mime="application/pdf",
                type="primary",
                key="btn_download_clean",
            )
