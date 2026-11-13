import os
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = Path(os.environ.get("ROBO_CONFIG", RAIZ / "config.toml"))


def carregar() -> dict:
    with open(ARQUIVO, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("busca", {})
    cfg.setdefault("robo", {})
    cfg.setdefault("fontes", {})
    cfg.setdefault("ia", {})
    cfg.setdefault("telegram", {})
    # Segredos só por variável de ambiente (nunca no arquivo).
    cfg["telegram"]["token"] = os.environ.get("TELEGRAM_TOKEN", "")
    cfg["telegram"]["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    return cfg
