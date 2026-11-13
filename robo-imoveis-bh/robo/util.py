import logging
import random
import re
import time
import unicodedata

import requests

log = logging.getLogger("robo")

USER_AGENT = "Mozilla/5.0 (compatible; robo-imoveis-bh/1.0; uso pessoal)"


def normalizar(texto: str | None) -> str:
    """'Santa Amélia (Pampulha)' -> 'santa amelia'"""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def para_int(valor) -> int | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return int(valor)
    m = re.search(r"\d[\d.]*", str(valor))
    if not m:
        return None
    try:
        return int(m.group(0).replace(".", ""))
    except ValueError:
        return None


class Navegador:
    """Sessão HTTP educada: identifica o robô, espera entre requisições e tenta de novo em falhas leves."""

    def __init__(self, pausa: float = 4.0):
        self.pausa = pausa
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "pt-BR,pt;q=0.9",
        })
        self._ultima = 0.0

    def _esperar(self):
        falta = self.pausa + random.uniform(0, 1.5) - (time.time() - self._ultima)
        if falta > 0:
            time.sleep(falta)
        self._ultima = time.time()

    def get(self, url: str, binario: bool = False):
        for tentativa in range(3):
            self._esperar()
            try:
                r = self.s.get(url, timeout=30)
            except requests.RequestException as e:
                log.warning("Falha de rede em %s: %s", url, e)
                continue
            if r.status_code == 200:
                return r.content if binario else r.text
            if r.status_code in (403, 404, 410):
                log.info("HTTP %s em %s", r.status_code, url)
                return None
            log.warning("HTTP %s em %s (tentativa %d)", r.status_code, url, tentativa + 1)
            time.sleep(10 * (tentativa + 1))
        return None
