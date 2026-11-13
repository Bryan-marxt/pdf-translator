"""Avaliação das fotos com Claude: o imóvel é bonito? moderno? arejado ou sufocante?

Só roda se houver credencial da API da Anthropic (ANTHROPIC_API_KEY ou `ant auth login`).
"""
import base64
import json
import logging
import os
from pathlib import Path

from .util import Navegador

log = logging.getLogger("robo.ia")

ESQUEMA = {
    "type": "object",
    "properties": {
        "nota_beleza": {"type": "integer", "description": "0-10: quão bonito/bem cuidado é"},
        "nota_modernidade": {"type": "integer", "description": "0-10: acabamento moderno x antigo"},
        "nota_amplitude": {"type": "integer", "description": "0-10: sensação de espaço, luz e ventilação (10 = nada sufocante)"},
        "tem_varanda_visivel": {"type": "boolean"},
        "nota_geral": {"type": "integer", "description": "0-10: o quanto combina com o gosto da pessoa"},
        "resumo": {"type": "string", "description": "1 ou 2 frases em português, direto ao ponto"},
        "pontos_positivos": {"type": "array", "items": {"type": "string"}},
        "pontos_negativos": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["nota_beleza", "nota_modernidade", "nota_amplitude", "tem_varanda_visivel",
                 "nota_geral", "resumo", "pontos_positivos", "pontos_negativos"],
    "additionalProperties": False,
}

SISTEMA = """Você ajuda uma pessoa a escolher imóvel para alugar em Belo Horizonte.
Você recebe as fotos de um anúncio e os dados principais. Avalie com olhar crítico e honesto,
como um amigo exigente: anúncios costumam esconder defeitos, então desconfie de fotos escuras,
grande-angular exagerada, ausência de foto de cômodos importantes, acabamento antigo (azulejo
datado, piso de taco gasto, armários velhos), cômodos estreitos ou corredores escuros.
Responda sempre em português do Brasil.

Gosto da pessoa:
{gosto}"""


def _tipo_imagem(dados: bytes) -> str | None:
    if dados[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp"
    if dados[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


PASTA_REFERENCIAS = Path(__file__).resolve().parent.parent / "referencias"


def _referencias(maximo: int = 10) -> list[dict]:
    """Fotos de imóveis que a pessoa AMOU (pasta referencias/). Vão no início da mensagem, em cache."""
    blocos = []
    if not PASTA_REFERENCIAS.is_dir():
        return blocos
    arquivos = sorted(p for p in PASTA_REFERENCIAS.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    for p in arquivos[:maximo]:
        dados = p.read_bytes()
        mt = _tipo_imagem(dados)
        if mt:
            blocos.append({"type": "image", "source": {
                "type": "base64", "media_type": mt, "data": base64.standard_b64encode(dados).decode()}})
    if blocos:
        blocos.insert(0, {"type": "text", "text":
            "REFERÊNCIA DE GOSTO — estas fotos são de um imóvel que a pessoa adorou. "
            "Use como régua do padrão de acabamento e da sensação de espaço/luz que ela quer:"})
        blocos[-1]["cache_control"] = {"type": "ephemeral"}  # reaproveita entre avaliações
    return blocos


class AvaliadorIA:
    def __init__(self, cfg: dict):
        self.cfg = cfg.get("ia", {})
        self.cliente = None
        self.referencias = _referencias()
        if not self.cfg.get("ativo", True):
            return
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE") or Path.home().joinpath(".config", "anthropic").is_dir()):
            log.info("IA desligada: defina ANTHROPIC_API_KEY para ativar a avaliação das fotos.")
            return
        try:
            import anthropic
            self._anthropic = anthropic
            self.cliente = anthropic.Anthropic()
        except Exception as e:  # sem SDK ou credencial inválida
            log.info("IA desligada (%s). Defina ANTHROPIC_API_KEY para ativar.", e)
            self.cliente = None

    @property
    def ativo(self) -> bool:
        return self.cliente is not None

    def avaliar(self, im: dict, nav: Navegador) -> dict | None:
        fotos = (im.get("fotos") or [])[: self.cfg.get("fotos_por_imovel", 6)]
        conteudo = []
        n_fotos = 0
        if self.referencias:
            conteudo.extend(self.referencias)
            conteudo.append({"type": "text", "text": "AGORA, O IMÓVEL A AVALIAR — fotos do anúncio:"})
        for url in fotos:
            dados = nav.get(url, binario=True)
            if not dados or len(dados) > 4_500_000:
                continue
            mt = _tipo_imagem(dados)
            if not mt:
                continue
            n_fotos += 1
            conteudo.append({"type": "image", "source": {
                "type": "base64", "media_type": mt, "data": base64.standard_b64encode(dados).decode()}})
        if n_fotos == 0:
            return None

        dados_txt = (
            f"Tipo: {im.get('tipo')} · Bairro: {im.get('bairro')} · Aluguel: R$ {im.get('aluguel')} · "
            f"Total: R$ {im.get('custo_total') or '?'} · {im.get('area') or '?'} m² · "
            f"{im.get('quartos') or '?'} quartos · {im.get('banheiros') or '?'} banheiros · "
            f"{im.get('vagas') or '?'} vagas\nTítulo: {im.get('titulo')}\n"
            f"Descrição do anúncio: {(im.get('descricao') or '')[:2500]}\n"
            f"Comodidades: {', '.join(im.get('amenidades') or [])}"
        )
        conteudo.append({"type": "text", "text": dados_txt + "\n\nAvalie este imóvel pelas fotos."})

        try:
            resp = self.cliente.beta.messages.create(
                model=self.cfg.get("modelo", "claude-opus-5-5"),
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SISTEMA.format(gosto=self.cfg.get("gosto", "").strip()),
                output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
                messages=[{"role": "user", "content": conteudo}],
            )
        except self._anthropic.AuthenticationError:
            log.error("Chave da API da Anthropic inválida — IA desligada.")
            self.cliente = None
            return None
        except self._anthropic.RateLimitError:
            log.warning("Limite de uso da API atingido; tento de novo na próxima varredura.")
            return None
        except self._anthropic.APIStatusError as e:
            log.warning("Erro da API (%s): %s", e.status_code, e.message)
            return None
        except self._anthropic.APIConnectionError as e:
            log.warning("Sem conexão com a API: %s", e)
            return None
        except TypeError as e:  # SDK sem credencial resolvível
            log.error("IA desligada — credencial da API não encontrada (%s)", e)
            self.cliente = None
            return None

        if resp.stop_reason == "refusal":
            log.info("IA recusou avaliar %s", im.get("url"))
            return None
        texto = next((b.text for b in resp.content if b.type == "text"), None)
        if not texto:
            return None
        try:
            resultado = json.loads(texto)
        except ValueError:
            return None
        resultado["fotos_avaliadas"] = n_fotos
        return resultado
