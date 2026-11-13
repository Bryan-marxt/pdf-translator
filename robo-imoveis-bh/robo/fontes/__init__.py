import re

RE_VARANDA = re.compile(r"\b(varanda|sacada|terra[cç]o|varand[aã]o|deck)\b", re.I)


def tem_varanda(*textos) -> bool:
    return any(t and RE_VARANDA.search(t) for t in textos)
