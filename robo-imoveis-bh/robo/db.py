import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .config import RAIZ

CAMINHO = RAIZ / "dados" / "imoveis.db"
_lock = threading.Lock()

# Campos que vêm das fontes (o resto é estado do robô/usuário)
CAMPOS_FONTE = [
    "fonte", "id_fonte", "url", "titulo", "tipo", "bairro", "endereco",
    "aluguel", "condominio_iptu", "custo_total", "area", "quartos", "banheiros",
    "suites", "vagas", "varanda", "amenidades", "descricao", "fotos", "lat", "lng",
    "ref_externa",
]
CAMPOS_JSON = {"amenidades", "fotos", "motivos", "ia", "custos"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS imoveis (
    id TEXT PRIMARY KEY,
    fonte TEXT, id_fonte TEXT, url TEXT, titulo TEXT, tipo TEXT, bairro TEXT, endereco TEXT,
    aluguel INTEGER, condominio_iptu INTEGER, custo_total INTEGER, area INTEGER,
    quartos INTEGER, banheiros INTEGER, suites INTEGER, vagas INTEGER,
    varanda INTEGER, amenidades TEXT, descricao TEXT, fotos TEXT, lat REAL, lng REAL,
    ref_externa TEXT, duplicado_de TEXT, custos TEXT,
    detalhe_ok INTEGER DEFAULT 0,
    primeiro_visto TEXT, ultimo_visto TEXT, ativo INTEGER DEFAULT 1,
    aluguel_anterior INTEGER, preco_mudou_em TEXT,
    passa INTEGER DEFAULT 0, nota INTEGER DEFAULT 0, motivos TEXT,
    ia TEXT, ia_em TEXT,
    favorito INTEGER DEFAULT 0, descartado INTEGER DEFAULT 0, visto INTEGER DEFAULT 0,
    notificado INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS historico_precos (
    imovel_id TEXT, data TEXT, aluguel INTEGER, custo_total INTEGER
);
CREATE TABLE IF NOT EXISTS execucoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT, fim TEXT, novos INTEGER, atualizados INTEGER, avaliados_ia INTEGER,
    erros INTEGER, resumo TEXT
);
CREATE INDEX IF NOT EXISTS idx_ref ON imoveis(ref_externa);
"""


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conectar() -> sqlite3.Connection:
    Path(CAMINHO).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CAMINHO, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(ESQUEMA)
    try:  # bancos criados antes da coluna 'custos'
        con.execute("ALTER TABLE imoveis ADD COLUMN custos TEXT")
    except sqlite3.OperationalError:
        pass
    return con


def _serializar(campo, valor):
    if campo in CAMPOS_JSON and valor is not None:
        return json.dumps(valor, ensure_ascii=False)
    if campo == "varanda" and valor is not None:
        return 1 if valor else 0
    return valor


def linha_para_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for c in CAMPOS_JSON:
        if d.get(c):
            try:
                d[c] = json.loads(d[c])
            except (TypeError, ValueError):
                d[c] = None
    if d.get("varanda") is not None:
        d["varanda"] = bool(d["varanda"])
    return d


def salvar_anuncio(con, imovel: dict) -> str:
    """Insere ou atualiza. Retorna 'novo', 'preco' (mudou de preço) ou 'visto'."""
    iid = f"{imovel['fonte']}:{imovel['id_fonte']}"
    ts = agora()
    with _lock:
        atual = con.execute("SELECT * FROM imoveis WHERE id=?", (iid,)).fetchone()
        if atual is None:
            campos = ["id", "primeiro_visto", "ultimo_visto"] + [c for c in CAMPOS_FONTE if c in imovel]
            valores = [iid, ts, ts] + [_serializar(c, imovel[c]) for c in campos[3:]]
            con.execute(
                f"INSERT INTO imoveis ({','.join(campos)}) VALUES ({','.join('?' * len(campos))})",
                valores,
            )
            con.execute("INSERT INTO historico_precos VALUES (?,?,?,?)",
                        (iid, ts, imovel.get("aluguel"), imovel.get("custo_total")))
            con.commit()
            return "novo"

        resultado = "visto"
        sets = {"ultimo_visto": ts, "ativo": 1}
        for c in CAMPOS_FONTE:
            if c not in imovel or imovel[c] is None:
                continue
            # Não sobrescreve dados ricos do detalhe com dados pobres da listagem
            if atual["detalhe_ok"] and c in ("descricao", "amenidades", "fotos", "varanda") :
                continue
            sets[c] = _serializar(c, imovel[c])
        novo_preco = imovel.get("aluguel")
        if novo_preco and atual["aluguel"] and novo_preco != atual["aluguel"]:
            sets["aluguel_anterior"] = atual["aluguel"]
            sets["preco_mudou_em"] = ts
            sets["notificado"] = 0
            con.execute("INSERT INTO historico_precos VALUES (?,?,?,?)",
                        (iid, ts, novo_preco, imovel.get("custo_total")))
            resultado = "preco"
        con.execute(
            f"UPDATE imoveis SET {','.join(f'{k}=?' for k in sets)} WHERE id=?",
            list(sets.values()) + [iid],
        )
        con.commit()
        return resultado


def atualizar(con, iid: str, **campos):
    if not campos:
        return
    with _lock:
        con.execute(
            f"UPDATE imoveis SET {','.join(f'{k}=?' for k in campos)} WHERE id=?",
            [_serializar(k, v) for k, v in campos.items()] + [iid],
        )
        con.commit()


def todos(con, so_ativos=False) -> list[dict]:
    sql = "SELECT * FROM imoveis"
    if so_ativos:
        sql += " WHERE ativo=1"
    return [linha_para_dict(r) for r in con.execute(sql).fetchall()]


def obter(con, iid: str) -> dict | None:
    r = con.execute("SELECT * FROM imoveis WHERE id=?", (iid,)).fetchone()
    return linha_para_dict(r) if r else None


def historico(con, iid: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT data, aluguel, custo_total FROM historico_precos WHERE imovel_id=? ORDER BY data", (iid,))]


def marcar_fora_do_ar(con, dias: int):
    limite = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    with _lock:
        con.execute("UPDATE imoveis SET ativo=0 WHERE ultimo_visto < ?", (limite,))
        con.commit()


def registrar_execucao(con, inicio, novos, atualizados, avaliados, erros, resumo):
    with _lock:
        con.execute(
            "INSERT INTO execucoes (inicio, fim, novos, atualizados, avaliados_ia, erros, resumo) VALUES (?,?,?,?,?,?,?)",
            (inicio, agora(), novos, atualizados, avaliados, erros, resumo),
        )
        con.commit()


def ultima_execucao(con) -> dict | None:
    r = con.execute("SELECT * FROM execucoes ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None
