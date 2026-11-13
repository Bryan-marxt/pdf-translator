"""Liga o robô (varre os sites a cada X minutos) e o painel web.

    python run.py                # robô + painel em http://localhost:8000
    python run.py --rede         # painel acessível por outros aparelhos da sua rede (celular)
    python run.py --so-painel    # só o painel, sem varrer
"""
import argparse
import logging
from logging.handlers import RotatingFileHandler

from robo import coletor, config, db
from robo.web import app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rede", action="store_true", help="abre o painel para a rede local (0.0.0.0)")
    p.add_argument("--so-painel", action="store_true", help="não varre os sites, só mostra o painel")
    args = p.parse_args()

    (config.RAIZ / "dados").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(name)s: %(message)s", "%d/%m %H:%M:%S")
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    for h in (logging.StreamHandler(),
              RotatingFileHandler(config.RAIZ / "dados" / "robo.log", maxBytes=2_000_000, backupCount=2)):
        h.setFormatter(fmt)
        raiz.addHandler(h)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    cfg = config.carregar()
    if not args.so_painel:
        coletor.iniciar_em_segundo_plano(db.conectar())
    porta = cfg["robo"].get("porta", 8000)
    print(f"\n  Painel: http://localhost:{porta}\n")
    app.run(host="0.0.0.0" if args.rede else "127.0.0.1", port=porta, threaded=True)


if __name__ == "__main__":
    main()
