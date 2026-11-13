"""QuintoAndar — lê as páginas públicas de busca (/alugar/imovel/...), permitidas pelo robots.txt.

Cada página traz até 24 imóveis em JSON (__NEXT_DATA__), já com a lista de comodidades
(VARANDA, APARTAMENTO_COBERTURA, VISTA_LIVRE...).
"""
import json
import logging
import re

from ..util import Navegador, para_int
from . import tem_varanda

log = logging.getLogger("robo.quintoandar")

BASE = "https://www.quintoandar.com.br"
FOTO = BASE + "/img/med/"

TIPOS = {"Apartamento": "apartamento", "Casa": "casa", "CasaCondominio": "casa_condominio",
         "Cobertura": "cobertura", "Kitnet": "kitnet", "StudioOuKitnet": "kitnet"}

AREJADO = {"VISTA_LIVRE", "SOL_DA_MANHA", "SOL_DA_TARDE", "JANELAS_GRANDES", "PE_DIREITO_ALTO",
           "QUARTOS_E_CORREDORES_COM_PORTAS_AMPLAS"}


def _legivel(codigo: str) -> str:
    return codigo.replace("_", " ").capitalize()


def _extrair(html: str) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    estado = json.loads(m.group(1))["props"]["pageProps"].get("initialState", {})
    return [h for h in estado.get("houses", {}).values() if isinstance(h, dict) and h.get("id")]


def converter(h: dict) -> dict | None:
    if not h.get("forRent") or not h.get("rentPrice"):
        return None
    amen = list(h.get("amenities") or []) + list(h.get("installations") or [])
    tipo = TIPOS.get(h.get("type"), "outro")
    if "APARTAMENTO_COBERTURA" in amen:
        tipo = "cobertura"
    desc = h.get("shortRentDescription") or ""
    bairro = h.get("neighbourhood") or h.get("regionName") or ""
    quartos = para_int(h.get("bedrooms"))
    fotos = [FOTO + p["url"] for p in (h.get("photos") or []) if p.get("url")]
    if not fotos and h.get("banner"):
        fotos = [FOTO + h["banner"]]
    return {
        "fonte": "quintoandar",
        "id_fonte": str(h["id"]),
        "url": f"{BASE}/imovel/{h['id']}/alugar",
        "titulo": desc or f"{tipo.replace('_', ' ').title()} com {quartos} quartos em {bairro}",
        "tipo": tipo,
        "bairro": bairro.strip(),
        "endereco": (h.get("address") or {}).get("address"),
        "aluguel": para_int(h.get("rentPrice")),
        "condominio_iptu": para_int(h.get("condoIptu")),
        "custo_total": para_int(h.get("totalCost")),
        "area": para_int(h.get("area")),
        "quartos": quartos,
        "banheiros": para_int(h.get("bathrooms")),
        "suites": para_int(h.get("suites")),
        "vagas": para_int(h.get("parkingSpots")),
        "varanda": ("VARANDA" in amen) or tem_varanda(desc),
        "amenidades": [_legivel(a) for a in amen],
        "descricao": desc,
        "fotos": fotos,
        "ref_externa": f"quintoandar:{h['id']}",
    }


def coletar(cfg: dict, nav: Navegador):
    """Gera imóveis (dicts padronizados) de todas as buscas configuradas."""
    fonte = cfg["fontes"].get("quintoandar", {})
    vistos = set()
    for regiao in fonte.get("regioes", []):
        for tipo in fonte.get("tipos", ["apartamento", "casa", "casacondominio"]):
            url = f"{BASE}/alugar/imovel/{regiao}-belo-horizonte-mg-brasil/{tipo}/3-4-quartos"
            html = nav.get(url)
            if not html:
                continue
            try:
                casas = _extrair(html)
            except (ValueError, KeyError) as e:
                log.warning("Não consegui ler %s: %s", url, e)
                continue
            log.info("QuintoAndar %s/%s: %d anúncios", regiao, tipo, len(casas))
            for h in casas:
                if h["id"] in vistos:
                    continue
                vistos.add(h["id"])
                imovel = converter(h)
                if imovel:
                    yield imovel
