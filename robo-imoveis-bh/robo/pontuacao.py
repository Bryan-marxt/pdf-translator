"""Filtros e nota (0–100) de cada imóvel, de acordo com o gosto configurado."""
import re

from .util import normalizar

MODERNO = re.compile(
    r"moderno|moderna|reformad|novo|nova|rec[eé]m|porcelanato|planejad|gourmet|conceito aberto|"
    r"integrad|ilumina[cç][aã]o embutida|gesso|sanca|lavabo|ar[- ]condicionado|automa[cç]|"
    r"blindex|vidro|piscina|alto padr[aã]o|acabamento|spots?|\bled\b|granito|churrasqueira|inox|"
    r"arm[aá]rios? embutid|closet|revestimento 3d|filete|sobrado|duplex|lustre", re.I)
AREJADO = re.compile(
    r"amplo|ampla|espa[cç]os|arejad|ventilad|iluminad|claro|clara|luz natural|vista|"
    r"p[eé][- ]direito|janel[aã]o|janelas grandes|sol da manh|quintal|jardim|[aá]rea externa|terra[cç]o|livre", re.I)
NEGATIVO = re.compile(
    r"precisa de reforma|necessita reforma|antigo|antiga|fundos|sem janela|apertad|escur|"
    r"subsolo|semi[- ]?subsolo|barrac[aã]o|cômodo|comodo|kitnet|conjugad|aceito proposta para reforma|"
    r"\btaco\b|piso cer[aâ]mico antigo|piso (em |de )?cer[aâ]mica|cer[aâ]mica nos quartos", re.I)


def _bairro_status(bairro: str, cfg_busca: dict) -> str:
    b = normalizar(bairro)
    if not b:
        return "desconhecido"
    for prefixo in ("bairro ", "vila ", "conjunto "):
        if b.startswith(prefixo) and len(b) > len(prefixo) + 3:
            b_curto = b[len(prefixo):]
            break
    else:
        b_curto = b

    def bate(lista):
        nomes = {normalizar(x) for x in lista}
        return b in nomes or b_curto in nomes

    if bate(cfg_busca.get("bairros_favoritos", [])):
        return "favorito"
    if bate(cfg_busca.get("bairros_aceitos", [])):
        return "aceito"
    return "fora"


def avaliar(im: dict, cfg: dict) -> tuple[bool, int, list[str]]:
    """Retorna (passa_nos_filtros, nota 0-100, motivos)."""
    busca = cfg["busca"]
    motivos: list[str] = []
    reprovado = False

    def reprova(msg):
        nonlocal reprovado
        reprovado = True
        motivos.append("✗ " + msg)

    # ---------- filtros obrigatórios ----------
    tipo = im.get("tipo") or "outro"
    if tipo not in busca.get("tipos", []):
        reprova(f"tipo '{tipo}' não desejado")

    st = _bairro_status(im.get("bairro"), busca)
    if st == "fora":
        reprova(f"bairro {im.get('bairro')} fora da região")

    aluguel = im.get("aluguel") or 0
    if aluguel and aluguel > busca.get("aluguel_max", 10**9):
        reprova(f"aluguel R$ {aluguel} acima do máximo")
    if aluguel and aluguel < busca.get("aluguel_min", 0):
        reprova(f"aluguel R$ {aluguel} suspeito de baixo")
    total = im.get("custo_total") or (aluguel + (im.get("condominio_iptu") or 0))
    if total and total > busca.get("custo_total_max", 10**9):
        reprova(f"custo total R$ {total} acima do máximo")

    q = im.get("quartos")
    if q is not None and q < busca.get("quartos_min", 0):
        reprova(f"{q} quarto(s)")
    b = im.get("banheiros")
    if b is not None and b < busca.get("banheiros_min", 0):
        reprova(f"{b} banheiro(s)")
    area = im.get("area")
    if area and area < busca.get("area_min", 0):
        reprova(f"{area} m² — apertado")

    if busca.get("exigir_varanda", True) and im.get("varanda") is False:
        reprova("sem varanda")

    # ---------- nota ----------
    pts = 0.0
    texto = " ".join([im.get("titulo") or "", im.get("descricao") or "", " ".join(im.get("amenidades") or [])])

    if tipo == "cobertura":
        pts += 12; motivos.append("✓ cobertura")
    elif tipo in ("casa", "casa_condominio"):
        pts += 9
    if tipo == "casa_condominio":
        pts += 3; motivos.append("✓ condomínio fechado")

    if st == "favorito":
        pts += 12; motivos.append(f"✓ bairro favorito ({im.get('bairro')})")
    elif st == "aceito":
        pts += 6

    if area:
        pts += max(0, min(18, (area - 80) * 18 / 80))  # 80 m² = 0 · 160 m²+ = 18
        if area >= 120:
            motivos.append(f"✓ espaçoso ({area} m²)")
    if q and q >= 3:
        pts += 5
    if b and b >= 2:
        pts += 7; motivos.append(f"✓ {b} banheiros")
    if im.get("suites"):
        pts += 3
    if im.get("vagas"):
        pts += 3

    if im.get("varanda"):
        pts += 6; motivos.append("✓ varanda")
    elif im.get("varanda") is None:
        motivos.append("? varanda a confirmar")

    n_mod = len(set(m.lower() for m in MODERNO.findall(texto)))
    n_are = len(set(m.lower() for m in AREJADO.findall(texto)))
    n_neg = len(set(m.lower() for m in NEGATIVO.findall(texto)))
    pts += min(10, n_mod * 2.5)
    pts += min(10, n_are * 2.5)
    pts -= min(20, n_neg * 7)
    if n_mod >= 2:
        motivos.append("✓ descrição indica acabamento moderno")
    if n_are >= 2:
        motivos.append("✓ descrição indica ambiente arejado/iluminado")
    if n_neg:
        motivos.append("✗ descrição tem sinais negativos (" + ", ".join(sorted(set(m.lower() for m in NEGATIVO.findall(texto)))) + ")")

    # Até aqui: ~0-100 nas regras. Com IA: 55% regras + 45% fotos.
    nota_regras = max(0, min(100, pts * 100 / 95))
    ia = im.get("ia") or {}
    if isinstance(ia.get("nota_geral"), (int, float)):
        nota = 0.55 * nota_regras + 0.45 * ia["nota_geral"] * 10
        motivos.append(f"★ IA (fotos): {ia['nota_geral']}/10")
        if ia.get("nota_geral", 10) < 4:
            motivos.append("✗ IA achou o imóvel feio/apertado")
    else:
        # Sem olhar as fotos não dá pra ter certeza de que é bonito: a nota fica limitada a ~80.
        nota = nota_regras * 0.8
        motivos.append("? sem avaliação das fotos (nota limitada)")

    return (not reprovado), int(round(nota)), motivos
