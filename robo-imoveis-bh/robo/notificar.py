"""Avisos no Telegram (opcional)."""
import logging

import requests

log = logging.getLogger("robo.telegram")


def _brl(v):
    return f"R$ {v:,.0f}".replace(",", ".") if v else "?"


def _post(tg: dict, texto: str, markdown=True) -> bool:
    if not (tg.get("ativo") and tg.get("token") and tg.get("chat_id")):
        return False
    corpo = {"chat_id": tg["chat_id"], "text": texto}
    if markdown:
        corpo["parse_mode"] = "Markdown"
    try:
        r = requests.post(f"https://api.telegram.org/bot{tg['token']}/sendMessage", json=corpo, timeout=20)
        if r.status_code != 200 and markdown:
            return _post(tg, texto.replace("*", ""), markdown=False)  # Markdown quebrou? manda puro
        if r.status_code != 200:
            log.warning("Telegram respondeu %s: %s", r.status_code, r.text[:200])
        return r.status_code == 200
    except requests.RequestException as e:
        log.warning("Falha ao enviar Telegram: %s", e)
        return False


def enviar_texto(cfg: dict, texto: str) -> bool:
    return _post(cfg.get("telegram", {}), texto, markdown=False)


def enviar(cfg: dict, im: dict, motivo: str):
    tg = cfg.get("telegram", {})
    ia = im.get("ia") or {}
    linhas = [
        f"🏠 *{motivo}* — nota {im.get('nota')}",
        f"{(im.get('tipo') or '').replace('_', ' ').title()} · {im.get('bairro')}",
        f"{_brl(im.get('aluguel'))} aluguel · total {_brl(im.get('custo_total'))}",
        f"{im.get('area') or '?'} m² · {im.get('quartos') or '?'} qts · {im.get('banheiros') or '?'} banh · {im.get('vagas') or 0} vagas",
    ]
    if im.get("aluguel_anterior"):
        linhas.append(f"Antes: {_brl(im['aluguel_anterior'])}")
    if ia.get("resumo"):
        linhas.append(f"IA: {ia['resumo']}")
    linhas.append(im.get("url", ""))
    return _post(tg, "\n".join(linhas))
