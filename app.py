import io
import json
import os
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import tempfile as _tempfile

import fitz
import PIL.Image
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from processor import PDFProcessor

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Traduzir PDF Online Grátis | PDF Translator com IA",
    page_icon="📄",
    layout="wide",
    menu_items={
        "Get Help": "https://aistudio.google.com/apikey",
        "About": "Ferramenta gratuita para transcrever e traduzir PDFs usando Google Gemini AI.",
    },
)

# ── Microsoft Clarity + currency detection (single injection) ────────────────
components.html(
    """
    <script type="text/javascript">
        (function(c,l,a,r,i,t,y){
            c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
            t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
            y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);
        })(window, document, "clarity", "script", "wky30b9qxr");
    </script>
    <script>
    (function() {
        try {
            var lang = navigator.language || (navigator.languages && navigator.languages[0]) || 'en-US';
            var region = (lang.split('-')[1] || lang.split('_')[1] || '').toUpperCase();
            var map = {
                'BR':'BRL','US':'USD','GB':'GBP','AU':'AUD','CA':'CAD',
                'JP':'JPY','CN':'CNY','IN':'INR','MX':'MXN','AR':'ARS',
                'DE':'EUR','FR':'EUR','ES':'EUR','IT':'EUR','PT':'EUR',
                'NL':'EUR','BE':'EUR','AT':'EUR','CH':'CHF','SE':'SEK',
                'NO':'NOK','DK':'DKK','NZ':'NZD','SG':'SGD','HK':'HKD',
                'KR':'KRW','ZA':'ZAR','TH':'THB','PL':'PLN','TR':'TRY',
                'CO':'COP','CL':'CLP','PE':'PEN'
            };
            var currency = map[region] || 'USD';
            var url = new URL(window.parent.location.href);
            if (!url.searchParams.has('cur')) {
                url.searchParams.set('cur', currency);
                window.parent.history.replaceState({}, '', url.toString());
            }
        } catch(e) {}
    })();
    </script>
    """,
    height=0,
    scrolling=False,
)

# ── SEO meta tags ─────────────────────────────────────────────────────────────
st.markdown(
    """
    <meta name="description" content="Traduza e transcreva qualquer PDF online grátis usando IA. Funciona com PDFs escaneados e digitais, em qualquer idioma. Powered by Google Gemini." />
    <meta name="keywords" content="traduzir pdf online grátis, converter pdf para texto, transcrever pdf escaneado, tradução de pdf com ia, google gemini pdf, pdf translator, pdf transcriber" />
    <meta property="og:title" content="Traduzir PDF Online Grátis | PDF Translator com IA" />
    <meta property="og:description" content="Transcreva e traduza qualquer PDF — escaneado ou digital — usando Google Gemini AI. Gratuito, sem cadastro." />
    <meta property="og:type" content="website" />
    <meta name="twitter:card" content="summary" />
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Model & currency definitions
# ---------------------------------------------------------------------------

TEXT_MODELS = {
    "gemini-2.5-flash-preview-04-17": {
        "label": "Gemini 2.5 Flash Preview ⭐",
        "note": "Recomendado — melhor custo-benefício em 2025",
        "input_per_m": 0.30,
        "output_per_m": 2.50,
    },
    "gemini-2.5-pro-preview-05-06": {
        "label": "Gemini 2.5 Pro Preview",
        "note": "Maior precisão, custo mais alto",
        "input_per_m": 1.25,
        "output_per_m": 10.00,
    },
    "gemini-2.0-flash": {
        "label": "Gemini 2.0 Flash",
        "note": "Econômico e rápido",
        "input_per_m": 0.10,
        "output_per_m": 0.40,
    },
    "gemini-2.0-flash-lite": {
        "label": "Gemini 2.0 Flash Lite",
        "note": "Mais barato disponível",
        "input_per_m": 0.075,
        "output_per_m": 0.30,
    },
    "gemini-1.5-flash": {
        "label": "Gemini 1.5 Flash (legado)",
        "note": "Modelo legado",
        "input_per_m": 0.075,
        "output_per_m": 0.30,
    },
    "gemini-1.5-pro": {
        "label": "Gemini 1.5 Pro (legado)",
        "note": "Premium legado",
        "input_per_m": 1.25,
        "output_per_m": 5.00,
    },
    "__custom__": {
        "label": "🔧 Modelo personalizado",
        "note": "",
        "input_per_m": None,
        "output_per_m": None,
    },
}

IMAGE_MODELS = {
    "gemini-2.0-flash-preview-image-generation": {
        "label": "Gemini 2.0 Flash Image",
        "note": "~$0,04 por imagem",
        "cost_per_image": 0.039,
    },
    "imagen-4.0-generate-001": {
        "label": "Imagen 4 — mais barato",
        "note": "~$0,02 por imagem",
        "cost_per_image": 0.020,
    },
    "__custom__": {
        "label": "🔧 Modelo personalizado",
        "note": "",
        "cost_per_image": None,
    },
}

CURRENCIES = {
    "BRL": ("Real Brasileiro",       "R$"),
    "USD": ("Dólar Americano",       "$"),
    "EUR": ("Euro",                  "€"),
    "GBP": ("Libra Esterlina",       "£"),
    "ARS": ("Peso Argentino",        "AR$"),
    "MXN": ("Peso Mexicano",         "MX$"),
    "CLP": ("Peso Chileno",          "CL$"),
    "COP": ("Peso Colombiano",       "CO$"),
    "PEN": ("Sol Peruano",           "S/"),
    "AUD": ("Dólar Australiano",     "A$"),
    "CAD": ("Dólar Canadense",       "C$"),
    "CHF": ("Franco Suíço",          "Fr"),
    "JPY": ("Iene Japonês",          "¥"),
    "CNY": ("Yuan Chinês",           "¥"),
    "INR": ("Rupia Indiana",         "₹"),
    "KRW": ("Won Coreano",           "₩"),
    "SGD": ("Dólar Singapura",       "S$"),
    "NZD": ("Dólar NZ",              "NZ$"),
    "ZAR": ("Rand Sul-Africano",     "R"),
    "TRY": ("Lira Turca",            "₺"),
    "PLN": ("Zloty Polonês",         "zł"),
    "SEK": ("Coroa Sueca",           "kr"),
    "NOK": ("Coroa Norueguesa",      "kr"),
}

# Tokens estimated per page (image PDF use case)
_TOKENS_IN  = 760   # ~560 image + ~200 prompt
_TOKENS_OUT = 800   # average transcription/translation output

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_desktop = Path.home() / "Desktop"
DESKTOP = _desktop if _desktop.exists() else Path(_tempfile.gettempdir())

EMPTY_PATTERNS = ["[página sem texto]", "notas de rodapé:", "[erro ao processar"]


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


@st.cache_data(ttl=3600)
def get_exchange_rates() -> dict:
    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as r:
            return json.loads(r.read()).get("rates", {})
    except Exception:
        return {}


@st.cache_data(show_spinner=False)
def count_pdf_pages(file_bytes: bytes) -> int:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    n = doc.page_count
    doc.close()
    return n


def calc_cost_usd(pages: int, tinfo: dict, iinfo: dict | None, n_images: int = 0) -> dict:
    inp_price = tinfo.get("input_per_m") or 0.0
    out_price = tinfo.get("output_per_m") or 0.0
    input_cost  = pages * _TOKENS_IN  * inp_price / 1_000_000
    output_cost = pages * _TOKENS_OUT * out_price / 1_000_000
    img_cost    = (n_images * iinfo["cost_per_image"]) if (iinfo and iinfo.get("cost_per_image")) else 0.0
    return {"input": input_cost, "output": output_cost, "images": img_cost,
            "total": input_cost + output_cost + img_cost}


def fmt_currency(value: float, code: str, symbol: str) -> str:
    if code in ("JPY", "KRW"):
        return f"{symbol}{value:,.0f}"
    return f"{symbol} {value:,.2f}"


# ---------------------------------------------------------------------------
# Hero section
# ---------------------------------------------------------------------------

st.markdown(
    """
    <h1 style='text-align:center;margin-bottom:0'>📄 Traduzir PDF Online Grátis</h1>
    <p style='text-align:center;color:#888;margin-top:4px;font-size:1.05rem'>
        Transcreva e traduza qualquer PDF com Inteligência Artificial — escaneados ou digitais, em qualquer idioma.
    </p>
    """,
    unsafe_allow_html=True,
)

col_f1, col_f2, col_f3, col_f4 = st.columns(4)
with col_f1:
    st.markdown("#### 🔍 PDFs Escaneados\nLê páginas digitalizadas como OCR, preservando a formatação original.")
with col_f2:
    st.markdown("#### 🌐 +10 Idiomas\nTraduz para Português, Inglês, Espanhol, Francês, Japonês e mais.")
with col_f3:
    st.markdown("#### ⚡ Processamento Paralelo\nVárias páginas ao mesmo tempo — documentos longos em minutos.")
with col_f4:
    st.markdown("#### 💾 Cache Automático\nInterrompeu? Retoma de onde parou sem reprocessar.")

st.markdown("---")

tab_transcribe, tab_remove = st.tabs(["🌐 Transcrever / Traduzir", "✂️ Remover Páginas"])

# ===========================================================================
# TAB 1 — Transcrever / Traduzir
# ===========================================================================

with tab_transcribe:
    with st.sidebar:
        st.header("⚙️ Configuração")

        # ── API Key ───────────────────────────────────────────────────────
        with st.expander("🔑 Como obter a chave da API (grátis)", expanded=False):
            st.markdown(
                """
**Passo a passo:**

1. Acesse **[aistudio.google.com/apikey](https://aistudio.google.com/apikey)**
2. Faça login com sua conta Google
3. Clique em **"Create API key"**
4. Copie a chave gerada (começa com `AIza...`)
5. Cole no campo abaixo

> O plano gratuito já é suficiente para centenas de páginas por dia.
                """
            )

        api_key = st.text_input(
            "Chave da API Gemini",
            type="password",
            value=os.getenv("GEMINI_API_KEY", ""),
            placeholder="AIza...",
            help="Obtenha gratuitamente em aistudio.google.com/apikey",
        )
        if not api_key:
            st.warning("⬆️ Insira sua chave da API para começar.")
        else:
            st.success("✅ Chave configurada.")

        st.markdown("---")
        st.info(
            "🔒 **Privacidade:** Seu PDF é processado temporariamente e enviado ao Google Gemini. "
            "Não armazenamos seus arquivos nem sua chave de API."
        )
        st.markdown("---")

        # ── Modelo de texto ───────────────────────────────────────────────
        st.markdown("**🤖 Modelo de transcrição**")
        tm_keys   = list(TEXT_MODELS.keys())
        tm_labels = [TEXT_MODELS[k]["label"] for k in tm_keys]

        sel_tm_idx = st.selectbox(
            "Modelo de texto",
            range(len(tm_keys)),
            format_func=lambda i: tm_labels[i],
            index=0,
            label_visibility="collapsed",
            key="sel_text_model",
        )
        sel_tm_key  = tm_keys[sel_tm_idx]
        text_model_info = TEXT_MODELS[sel_tm_key]

        if text_model_info["note"]:
            st.caption(f"ℹ️ {text_model_info['note']}")
        if text_model_info["input_per_m"] is not None:
            st.caption(
                f"Preço: entrada **${text_model_info['input_per_m']}/M tokens** · "
                f"saída **${text_model_info['output_per_m']}/M tokens**"
            )

        if sel_tm_key == "__custom__":
            model_name = st.text_input(
                "Nome do modelo",
                placeholder="ex: gemini-2.5-flash-preview-04-17",
                key="custom_text_model",
            )
        else:
            model_name = sel_tm_key

        st.markdown("---")

        # ── Tradução ──────────────────────────────────────────────────────
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

        # ── Modelo de imagem ──────────────────────────────────────────────
        translate_images = st.checkbox(
            "🎨 Traduzir imagens/desenhos",
            value=False,
            help="Usa IA de geração de imagens para traduzir texto dentro de ilustrações e diagramas.",
        )
        img_model_info: dict | None = None
        image_model = "gemini-2.0-flash-preview-image-generation"

        if translate_images:
            st.markdown("**Modelo de imagem**")
            im_keys   = list(IMAGE_MODELS.keys())
            im_labels = [IMAGE_MODELS[k]["label"] for k in im_keys]

            sel_im_idx = st.selectbox(
                "Modelo de imagem",
                range(len(im_keys)),
                format_func=lambda i: im_labels[i],
                index=0,
                label_visibility="collapsed",
                key="sel_image_model",
            )
            sel_im_key  = im_keys[sel_im_idx]
            img_model_info = IMAGE_MODELS[sel_im_key]

            if img_model_info["note"]:
                st.caption(f"ℹ️ {img_model_info['note']}")

            if sel_im_key == "__custom__":
                image_model = st.text_input(
                    "Nome do modelo de imagem",
                    placeholder="ex: gemini-2.0-flash-preview-image-generation",
                    key="custom_image_model",
                )
            else:
                image_model = sel_im_key

            if img_model_info.get("cost_per_image"):
                st.warning(f"⚠️ ~${img_model_info['cost_per_image']:.3f} USD por imagem gerada")

        st.markdown("---")

        # ── Calculadora de custo ──────────────────────────────────────────
        st.markdown("**💰 Calculadora de Custo**")

        detected_cur = st.query_params.get("cur", "BRL")
        if detected_cur not in CURRENCIES:
            detected_cur = "BRL"

        cur_keys   = list(CURRENCIES.keys())
        cur_labels = [f"{k} — {CURRENCIES[k][0]}" for k in cur_keys]
        cur_default_idx = cur_keys.index(detected_cur)

        sel_cur_idx = st.selectbox(
            "Moeda",
            range(len(cur_keys)),
            format_func=lambda i: cur_labels[i],
            index=cur_default_idx,
            key="currency_sel",
        )
        selected_currency = cur_keys[sel_cur_idx]
        cur_name, cur_symbol = CURRENCIES[selected_currency]

        rates = get_exchange_rates()
        rate  = rates.get(selected_currency, 1.0)

        if selected_currency != "USD":
            if rate != 1.0:
                st.caption(f"💱 1 USD = {rate:.2f} {selected_currency}")
            else:
                st.caption("⚠️ Taxa de câmbio indisponível agora.")

        st.caption("*Estimativa. Páginas com mais texto podem custar mais.*")

    # -----------------------------------------------------------------------
    # Upload
    # -----------------------------------------------------------------------

    st.markdown("### 📤 Enviar PDF")
    st.markdown(
        "Suporta **PDFs escaneados** (imagens digitalizadas) e **PDFs com texto nativo**. "
        "Tamanho máximo: 500 MB."
    )

    uploaded_file = st.file_uploader(
        "Arraste o PDF aqui ou clique para selecionar",
        type="pdf",
        help="Funciona com PDFs escaneados e com texto nativo.",
        key="transcribe_uploader",
    )

    force_reprocess = False

    if uploaded_file:
        size_kb = uploaded_file.size / 1024
        st.info(f"**Arquivo:** {uploaded_file.name} — {size_kb:.1f} KB")

        # ── Cache raw bytes + count pages (once per file) ─────────────────
        fid = uploaded_file.file_id
        if f"raw_{fid}" not in st.session_state:
            st.session_state[f"raw_{fid}"] = uploaded_file.read()
        raw_bytes  = st.session_state[f"raw_{fid}"]
        page_count = count_pdf_pages(raw_bytes)

        # ── Cache status ──────────────────────────────────────────────────
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

        # ── Cost estimate ─────────────────────────────────────────────────
        st.markdown("#### 💰 Custo Estimado")

        if text_model_info.get("input_per_m") is not None:
            est_images = max(1, page_count // 3) if (translate_images and img_model_info) else 0
            cost = calc_cost_usd(page_count, text_model_info, img_model_info if translate_images else None, est_images)
            total_local = cost["total"] * rate

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Páginas", page_count)
            mc2.metric("Modelo", text_model_info["label"].split("⭐")[0].strip())
            mc3.metric("Total (USD)", f"${cost['total']:.4f}")
            mc4.metric(f"Total ({selected_currency})", fmt_currency(total_local, selected_currency, cur_symbol))

            with st.expander("Ver detalhamento do custo"):
                sym = cur_symbol
                rows = [
                    ("Entrada (imagem + prompt)", _TOKENS_IN, page_count,
                     cost["input"], cost["input"] * rate),
                    ("Saída (texto transcrito)", _TOKENS_OUT, page_count,
                     cost["output"], cost["output"] * rate),
                ]
                if cost["images"] > 0:
                    rows.append((f"Imagens geradas (~{est_images} imgs)", "—", "—",
                                 cost["images"], cost["images"] * rate))

                header = f"| Item | Tokens/pág | Páginas | USD | {selected_currency} |"
                sep    = "|---|---|---|---|---|"
                body   = "\n".join(
                    f"| {r[0]} | {r[1]} | {r[2]} | ${r[3]:.5f} | {fmt_currency(r[4], selected_currency, sym)} |"
                    for r in rows
                )
                total_row = (
                    f"| **Total** | | | **${cost['total']:.5f}** | "
                    f"**{fmt_currency(total_local, selected_currency, sym)}** |"
                )
                st.markdown(f"{header}\n{sep}\n{body}\n{total_row}")
                st.caption(
                    f"Baseado em {_TOKENS_IN} tokens de entrada e {_TOKENS_OUT} tokens de saída por página. "
                    "Preços conforme tabela oficial Google AI Studio (mai/2025)."
                )
        else:
            st.info("Informe o modelo personalizado na barra lateral para ver o custo estimado.")

    elif not api_key:
        st.warning("👈 Insira sua chave da API Gemini na barra lateral para começar.")

    # -----------------------------------------------------------------------
    # Process button
    # -----------------------------------------------------------------------

    can_process = bool(uploaded_file and api_key)

    if st.button("🚀 Processar PDF", disabled=not can_process, type="primary", key="btn_process"):

        cache_dir = get_cache_dir(uploaded_file.name, uploaded_file.size, target_language)

        if force_reprocess and cache_dir.exists():
            for f in cache_dir.glob("pagina_*"):
                f.unlink(missing_ok=True)
            st.toast("Cache apagado — processando do zero.", icon="🗑️")

        processor = PDFProcessor(api_key=api_key, model_name=model_name, cache_dir=cache_dir)

        pdf_bytes = st.session_state.get(f"raw_{uploaded_file.file_id}") or uploaded_file.read()
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
                status_line  = st.empty()
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

                st.write("🔗 Costurando frases partidas entre páginas…")
                from processor import stitch_boundaries, clean_fake_footnotes
                texts = stitch_boundaries(texts)
                texts = [clean_fake_footnotes(t) for t in texts]

                st.write("🖼️ Extraindo imagens embutidas do PDF…")
                native_images = processor.extract_native_images(tmp_path)
                total_imgs = sum(len(v) for v in native_images.values())
                img_pages  = len(native_images)
                if img_pages:
                    st.write(f"✅ {img_pages} página(s) com {total_imgs} imagem(ns) encontradas.")
                else:
                    st.write("ℹ️ Nenhuma imagem embutida encontrada.")

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
                    for page_num, imgs in native_images.items():
                        page_images[page_num] = [(img, None) for img in imgs]

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

            stem     = Path(uploaded_file.name).stem
            lang_tag = target_language.split()[0].lower() if target_language else "transcrito"
            out_name = f"{stem}_{lang_tag}.pdf"

            st.success(f"PDF pronto: **{out_name}**")
            st.download_button(
                "⬇️ Baixar PDF",
                data=output_bytes,
                file_name=out_name,
                mime="application/pdf",
                type="primary",
            )
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
        "e gere um PDF limpo — **sem usar IA, sem custo**."
    )

    remove_file = st.file_uploader(
        "Carregar PDF para editar",
        type="pdf",
        key="remove_uploader",
    )

    if remove_file:
        file_id  = remove_file.file_id
        sk_thumbs = f"rm_thumbs_{file_id}"
        sk_texts  = f"rm_texts_{file_id}"
        sk_total  = f"rm_total_{file_id}"
        sk_data   = f"rm_data_{file_id}"

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

        st.markdown(f"**{total} páginas** encontradas.")

        ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([2, 2, 2, 2, 2])

        with ctrl1:
            cols_count = st.select_slider("Colunas na grade", options=[2, 3, 4, 5, 6], value=5)
        with ctrl2:
            if st.button("🤖 Marcar vazias automaticamente"):
                for i in range(total):
                    ne = _is_native_empty(native_texts[i])
                    ce = _is_cache_empty(i + 1, remove_file.name, remove_file.size)
                    st.session_state[f"rm_{file_id}_{i}"] = ne or ce
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

        for i in range(total):
            key = f"rm_{file_id}_{i}"
            if key not in st.session_state:
                st.session_state[key] = _is_native_empty(native_texts[i])

        for row_start in range(0, total, cols_count):
            cols = st.columns(cols_count)
            for col_idx, page_idx in enumerate(range(row_start, min(row_start + cols_count, total))):
                key       = f"rm_{file_id}_{page_idx}"
                is_marked = st.session_state.get(key, False)

                with cols[col_idx]:
                    if is_marked:
                        st.markdown(
                            "<div style='border:3px solid #e74c3c;border-radius:6px;padding:2px;'>",
                            unsafe_allow_html=True,
                        )
                    st.image(thumbs[page_idx], use_container_width=True)
                    if is_marked:
                        st.markdown("</div>", unsafe_allow_html=True)
                    label = f"🗑️ **Pág. {page_idx + 1}**" if is_marked else f"Pág. {page_idx + 1}"
                    st.checkbox(label, key=key)

        st.markdown("---")

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
            raw      = st.session_state[sk_data]
            src_doc  = fitz.open(stream=raw, filetype="pdf")
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
