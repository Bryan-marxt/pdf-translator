"""Chaves na Mão — lê as páginas públicas por bairro (sem parâmetros '?', que o robots.txt proíbe).

A listagem traz preço, quartos, banheiros, área e coordenadas (JSON-LD). A página de detalhe
traz descrição completa, comodidades (varanda etc.) e todas as fotos — o robô só abre o detalhe
dos imóveis que já passaram nos filtros básicos.
"""
import json
import logging
import re

from ..util import Navegador, para_int
from . import tem_varanda

log = logging.getLogger("robo.chavesnamao")

BASE = "https://www.chavesnamao.com.br"


def _jsonld(html: str) -> list[dict]:
    saida = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            saida.append(json.loads(m.group(1).strip()))
        except ValueError:
            pass
    return saida


def _tipo_pela_url(url: str) -> str:
    slug = url.split("/imovel/", 1)[-1]
    if slug.startswith("cobertura"):
        return "cobertura"
    if slug.startswith(("casa-de-condominio", "casa-em-condominio", "sobrado-de-condominio", "sobrado-em-condominio")):
        return "casa_condominio"
    if slug.startswith(("casa-para", "sobrado")):
        return "casa"
    if slug.startswith(("apartamento", "flat", "loft", "duplex", "triplex")):
        return "apartamento"
    return "outro"


def _converter_lista(oferta: dict) -> dict | None:
    url = oferta.get("url", "")
    m = re.search(r"/id-(\d+)/?", url)
    if not m:
        return None
    item = oferta.get("itemOffered") or {}
    end = item.get("address") or {}
    geo = item.get("geo") or {}
    lat, lng = geo.get("latitude"), geo.get("longitude")
    try:
        lat, lng = float(lat), float(lng)
        if lat == 0 or lng == 0:
            lat = lng = None
    except (TypeError, ValueError):
        lat = lng = None
    area = para_int((item.get("floorSize") or {}).get("unitText"))
    if not area:
        area = para_int(re.search(r"-(\d+)m2-", url).group(1)) if re.search(r"-(\d+)m2-", url) else None
    rua = (end.get("streetAddress") or "").replace("Rua: ", "").strip(" ,")
    return {
        "fonte": "chavesnamao",
        "id_fonte": m.group(1),
        "url": url,
        "titulo": oferta.get("name"),
        "tipo": _tipo_pela_url(url),
        "bairro": (end.get("addressLocality") or "").split(",")[0].strip(),
        "endereco": None if "não disponível" in rua else rua,
        "aluguel": para_int(oferta.get("price")),
        "area": area,
        "quartos": para_int(item.get("numberOfBedrooms")),
        "banheiros": para_int(item.get("numberOfBathroomsTotal")),
        "fotos": [item["image"]] if item.get("image") else [],
        "lat": lat,
        "lng": lng,
        "varanda": True if tem_varanda(oferta.get("name")) else None,  # None = ainda não sabemos
    }


def _ler_lista(html: str) -> tuple[int, list[dict]]:
    for d in _jsonld(html):
        if d.get("@type") == "RealEstateListing" and "offers" in d:
            ofertas = d["offers"].get("itemListElement") or []
            return d["offers"].get("numberOfItems") or 0, ofertas
    return 0, []


def coletar(cfg: dict, nav: Navegador):
    fonte = cfg["fontes"].get("chavesnamao", {})
    vistos = set()
    for bairro in fonte.get("bairros", []):
        for tipo in fonte.get("tipos", ["apartamentos", "coberturas", "casas", "casas-em-condominio"]):
            base = f"{BASE}/{tipo}-para-alugar/mg-belo-horizonte/{bairro}/"
            html = nav.get(base)
            if not html:
                continue
            total, ofertas = _ler_lista(html)
            # Página só mostra 30; se tem mais, refina por número de quartos (URL sem '?')
            if total > len(ofertas):
                for q in ("3-quartos", "4-quartos"):
                    h2 = nav.get(base + q + "/")
                    if h2:
                        ofertas += _ler_lista(h2)[1]
            log.info("Chaves na Mão %s/%s: %d anúncios (total no site: %d)", bairro, tipo, len(ofertas), total)
            for o in ofertas:
                imovel = _converter_lista(o)
                if imovel and imovel["id_fonte"] not in vistos:
                    vistos.add(imovel["id_fonte"])
                    yield imovel


def detalhar(url: str, nav: Navegador) -> dict | None:
    """Abre a página do anúncio e devolve os campos extras."""
    html = nav.get(url)
    if not html:
        return None
    for d in _jsonld(html):
        for no in d.get("@graph", []):
            if no.get("@type") != "RealEstateListing":
                continue
            sobre = no.get("about") or {}
            oferta = sobre.get("offers") or {}
            item = oferta.get("itemOffered") or {}
            descricao = oferta.get("description") or no.get("description") or ""
            resumo = sobre.get("description") or ""
            amen = [a.get("name") for a in item.get("amenityFeature") or [] if a.get("value") and a.get("name")]
            cond = re.search(r"Condom[ií]nio:\s*R\$\s*([\d.]+)", descricao)
            iptu = re.search(r"IPTU:\s*R\$\s*([\d.]+)", descricao)
            cond_iptu = (para_int(cond.group(1)) if cond else 0) + (para_int(iptu.group(1)) if iptu else 0)
            vagas = re.search(r"(\d+)\s+garage", resumo)
            ref = re.search(r"ref:? do im[oó]vel:?\s*([\w-]+)", resumo)
            extra = {
                "descricao": descricao,
                "amenidades": amen,
                "fotos": sobre.get("image") or [],
                "varanda": tem_varanda(" ".join(amen), descricao),
                "banheiros": para_int(item.get("numberOfBathroomsTotal")),
                "vagas": para_int(vagas.group(1)) if vagas else None,
                "condominio_iptu": cond_iptu or None,
                "titulo": sobre.get("name"),
            }
            if ref and "QUINTOANDAR" in resumo.upper():
                extra["ref_externa"] = f"quintoandar:{ref.group(1)}"
            return {k: v for k, v in extra.items() if v not in (None, [], "")} | {"varanda": extra["varanda"]}
    return None
