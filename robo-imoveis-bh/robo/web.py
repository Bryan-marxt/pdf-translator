"""Painel web: http://localhost:8000"""
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from . import coletor, config, db, pontuacao

ESTATICO = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=None)

CAMPOS_LISTA = [
    "id", "fonte", "url", "titulo", "tipo", "bairro", "endereco", "aluguel", "condominio_iptu",
    "custo_total", "area", "quartos", "banheiros", "suites", "vagas", "varanda", "fotos",
    "primeiro_visto", "ultimo_visto", "ativo", "aluguel_anterior", "preco_mudou_em",
    "passa", "nota", "motivos", "ia", "favorito", "descartado", "visto", "lat", "lng",
]


def _con():
    return db.conectar()


@app.get("/")
def index():
    return send_from_directory(ESTATICO, "index.html")


@app.get("/static/<path:nome>")
def estatico(nome):
    return send_from_directory(ESTATICO, nome)


@app.get("/api/imoveis")
def listar():
    con = _con()
    try:
        itens = []
        cfg = config.carregar()
        for im in db.todos(con):
            if im.get("duplicado_de"):
                continue
            d = {k: im.get(k) for k in CAMPOS_LISTA}
            d["fotos"] = (im.get("fotos") or [])[:8]
            d["custo_mensal"] = pontuacao.custo_mensal(im, cfg)
            itens.append(d)
        return jsonify(itens)
    finally:
        con.close()


@app.get("/api/imoveis/<path:iid>")
def detalhe(iid):
    con = _con()
    try:
        im = db.obter(con, iid)
        if not im:
            abort(404)
        im["historico"] = db.historico(con, iid)
        im["custo_mensal"] = pontuacao.custo_mensal(im, config.carregar())
        return jsonify(im)
    finally:
        con.close()


@app.post("/api/imoveis/<path:iid>")
def marcar(iid):
    dados = request.get_json(silent=True) or {}
    campos = {k: int(bool(dados[k])) for k in ("favorito", "descartado", "visto") if k in dados}
    con = _con()
    try:
        if not db.obter(con, iid):
            abort(404)
        db.atualizar(con, iid, **campos)
        return jsonify({"ok": True})
    finally:
        con.close()


@app.get("/api/status")
def status():
    con = _con()
    try:
        cfg = config.carregar()
        return jsonify({
            **coletor.estado,
            "intervalo_minutos": cfg["robo"].get("intervalo_minutos", 30),
            "ultima_execucao": db.ultima_execucao(con),
            "telegram": bool(cfg["telegram"].get("ativo") and cfg["telegram"].get("token")),
            "bairros": cfg["busca"].get("bairros_favoritos", []) + cfg["busca"].get("bairros_aceitos", []),
        })
    finally:
        con.close()


@app.post("/api/varrer")
def varrer():
    coletor.rodar_agora()
    return jsonify({"ok": True})
