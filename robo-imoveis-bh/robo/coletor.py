"""Uma varredura completa: busca nos sites → salva → detalha → avalia fotos → dá nota → avisa."""
import logging
import threading
import time
from datetime import datetime, timedelta

from . import config, db, notificar, pontuacao
from .fontes import chavesnamao, quintoandar
from .ia import AvaliadorIA
from .util import Navegador

log = logging.getLogger("robo")

FONTES = {"quintoandar": quintoandar, "chavesnamao": chavesnamao}

# Estado compartilhado com o painel
estado = {"rodando": False, "etapa": "", "proxima": None, "ultima": None}
_acordar = threading.Event()


def _repontuar(con, cfg, ids=None):
    lista = db.todos(con) if ids is None else [db.obter(con, i) for i in ids]
    for im in lista:
        if not im:
            continue
        passa, nota, motivos = pontuacao.avaliar(im, cfg)
        if passa != bool(im["passa"]) or nota != im["nota"] or motivos != im.get("motivos"):
            db.atualizar(con, im["id"], passa=int(passa), nota=nota, motivos=motivos)


def _marcar_duplicados(con):
    # Chaves na Mão republica anúncios do QuintoAndar: fica só a versão do QuintoAndar.
    with db._lock:
        con.execute("""
            UPDATE imoveis SET duplicado_de = (
                SELECT q.id FROM imoveis q WHERE q.fonte='quintoandar' AND q.ref_externa = imoveis.ref_externa)
            WHERE fonte != 'quintoandar' AND ref_externa IS NOT NULL AND duplicado_de IS NULL""")
        con.commit()


def varrer(con) -> dict:
    cfg = config.carregar()
    nav = Navegador(cfg["robo"].get("pausa_entre_requisicoes", 4))
    inicio = db.agora()
    primeira_vez = db.ultima_execucao(con) is None
    novos = atualizados = erros = avaliados = 0
    resumo = []

    # 1) Listagens
    for nome, mod in FONTES.items():
        if not cfg["fontes"].get(nome, {}).get("ativo", False):
            continue
        estado["etapa"] = f"Buscando no {nome}"
        n_fonte = 0
        try:
            for im in mod.coletar(cfg, nav):
                r = db.salvar_anuncio(con, im)
                n_fonte += 1
                if r == "novo":
                    novos += 1
                elif r == "preco":
                    atualizados += 1
        except Exception:
            erros += 1
            log.exception("Erro na fonte %s", nome)
        resumo.append(f"{nome}: {n_fonte} anúncios")

    _repontuar(con, cfg)

    # 2) Detalhes do Chaves na Mão só para quem passou nos filtros básicos
    if cfg["fontes"].get("chavesnamao", {}).get("ativo"):
        estado["etapa"] = "Abrindo detalhes dos anúncios"
        pendentes = [im for im in db.todos(con, so_ativos=True)
                     if im["fonte"] == "chavesnamao" and not im["detalhe_ok"] and im["passa"]]
        pendentes.sort(key=lambda x: -x["nota"])
        for im in pendentes[: cfg["fontes"]["chavesnamao"].get("detalhes_por_ciclo", 40)]:
            try:
                extra = chavesnamao.detalhar(im["url"], nav)
            except Exception:
                log.exception("Erro no detalhe %s", im["url"])
                extra = None
            if extra is None:
                continue
            extra["detalhe_ok"] = 1
            if extra.get("condominio_iptu") and im.get("aluguel"):
                extra["custo_total"] = im["aluguel"] + extra["condominio_iptu"]
            db.atualizar(con, im["id"], **extra)
        _marcar_duplicados(con)
        _repontuar(con, cfg)

    # 3) IA nas fotos dos melhores ainda não avaliados
    avaliador = AvaliadorIA(cfg)
    if avaliador.ativo:
        estado["etapa"] = "IA avaliando fotos"
        fila = [im for im in db.todos(con, so_ativos=True)
                if im["passa"] and not im["ia_em"] and not im["duplicado_de"] and not im["descartado"]]
        fila.sort(key=lambda x: -x["nota"])
        for im in fila[: cfg["ia"].get("max_avaliacoes_por_ciclo", 15)]:
            res = avaliador.avaliar(im, nav)
            if res:
                db.atualizar(con, im["id"], ia=res, ia_em=db.agora())
                avaliados += 1
                _repontuar(con, cfg, [im["id"]])
            if not avaliador.ativo:
                break
        resumo.append(f"IA: {avaliados} avaliados")
    else:
        resumo.append("IA: desligada (sem ANTHROPIC_API_KEY)")

    # 4) Avisos
    estado["etapa"] = "Enviando avisos"
    tg = cfg.get("telegram", {})
    candidatos = [im for im in db.todos(con, so_ativos=True)
                  if im["passa"] and not im["notificado"] and not im["duplicado_de"] and not im["descartado"]]
    if primeira_vez:
        bons = sum(1 for im in candidatos if im["nota"] >= tg.get("nota_minima", 65))
        for im in candidatos:
            db.atualizar(con, im["id"], notificado=1)
        notificar.enviar_texto(cfg, f"🤖 Robô de imóveis ligado! Encontrei {len(candidatos)} imóveis dentro do perfil "
                                    f"({bons} com nota ≥ {tg.get('nota_minima', 65)}). A partir de agora aviso só os novos.")
    else:
        for im in sorted(candidatos, key=lambda x: -x["nota"]):
            if im["nota"] < tg.get("nota_minima", 65):
                continue
            if im.get("preco_mudou_em") and im.get("aluguel_anterior"):
                if not tg.get("avisar_queda_preco", True) or im["aluguel"] >= im["aluguel_anterior"]:
                    db.atualizar(con, im["id"], notificado=1)
                    continue
                motivo = "Baixou o preço"
            else:
                motivo = "Imóvel novo"
            if notificar.enviar(cfg, im, motivo) or not tg.get("ativo"):
                db.atualizar(con, im["id"], notificado=1)

    db.marcar_fora_do_ar(con, cfg["robo"].get("dias_para_fora_do_ar", 4))
    db.registrar_execucao(con, inicio, novos, atualizados, avaliados, erros, " · ".join(resumo))
    log.info("Varredura concluída: %d novos, %d mudaram de preço, %d avaliados pela IA, %d erros",
             novos, atualizados, avaliados, erros)
    return {"novos": novos, "atualizados": atualizados, "avaliados": avaliados, "erros": erros}


def rodar_agora():
    _acordar.set()


def laco(con):
    """Fica varrendo para sempre, no intervalo configurado."""
    while True:
        estado["rodando"] = True
        try:
            varrer(con)
        except Exception:
            log.exception("Varredura falhou")
        estado["rodando"] = False
        estado["etapa"] = ""
        estado["ultima"] = db.agora()
        minutos = config.carregar()["robo"].get("intervalo_minutos", 30)
        estado["proxima"] = (datetime.now() + timedelta(minutes=minutos)).isoformat(timespec="seconds")
        _acordar.clear()
        _acordar.wait(timeout=minutos * 60)


def iniciar_em_segundo_plano(con):
    t = threading.Thread(target=laco, args=(con,), daemon=True, name="coletor")
    t.start()
    return t


if __name__ == "__main__":  # python -m robo.coletor  → uma varredura só
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    t0 = time.time()
    print(varrer(db.conectar()), f"{time.time() - t0:.0f}s")
