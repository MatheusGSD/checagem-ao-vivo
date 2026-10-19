"""Extração de termos compartilhada pelas análises (nuvens, palavras típicas, embeddings).

Regras: expressões compostas, números com unidade e siglas contam inteiros; o resto passa pelo
spaCy e fica o lema de substantivos, nomes próprios, verbos e adjetivos que não sejam palavra
vazia nem muleta de fala.
"""
import re
from collections import Counter

import spacy

nlp = spacy.load("pt_core_news_lg", disable=["ner", "parser"])

# muletas de fala e palavras vazias que o spaCy não marca como stopword
EXTRA_STOP = set("""
né aí então gente coisa ser ter estar fazer ir dizer falar vir dar ficar poder querer saber achar
olhar ver haver cá lá aqui ali agora hoje sim não tá tô pra pro senhor senhora candidato candidata
porque quer vai vou eh ah ã hum tipo bom ok vez cada todo tudo algum minuto obrigado olha deixar
""".split())


# nomes e expressões que contam como um termo só (regex sem distinção de maiúsculas -> forma exibida)
COMPOSTOS = [
    (r"careca do inss|careca", "Careca do INSS"),
    (r"roberta lu(?:ch|s)singer|lu(?:ch|s)singer|roberta", "Roberta Luchsinger"),
    (r"daniel vorcaro|vorcaro|daniel", "Daniel Vorcaro"),  # "Daniel" sozinho também é ele ("ó, Daniel, não vende o banco")
    (r"fl[aá]vio bolsonaro", "Flávio Bolsonaro"),  # separa o candidato de "Bolsonaro" (o pai)
    (r"pol[ií]cia federal", "Polícia Federal"),
    (r"bolsa fam[ií]lia", "Bolsa Família"),
    (r"banco central", "Banco Central"),
    (r"instituto lula", "Instituto Lula"),
    (r"escrit[oó]rio do crime", "Escritório do Crime"),
    (r"escala 6x1", "escala 6x1"),
    (r"andr[eé] mendon[cç]a", "André Mendonça"),
    (r"frei chico", "Frei Chico"),
    (r"jaques wagner", "Jaques Wagner"),
    (r"rui costa", "Rui Costa"),
    (r"crime organizado", "crime organizado"),
    (r"lavagem de dinheiro", "lavagem de dinheiro"),
    (r"fake news", "fake news"),
    (r"estados unidos", "Estados Unidos"),
    (r"congresso nacional", "Congresso Nacional"),
    (r"licen[cç]a[- ]de[- ]maternidade|licen[cç]a[- ]maternidade|aux[ií]lio de maternidade", "licença-maternidade"),
    (r"p[eé][- ]de[- ]meia", "Pé-de-Meia"),
    (r"terras ind[ií]genas", "terras indígenas"),
    (r"vini j[uú]nior", "Vini Jr."),
]
# números com unidade viram termo ("134 milhões", "73%")
NUMERO = re.compile(r"(?:R\$\s*)?\b(\d+(?:[.,]\d+)?)\s*(%|mil\b|milh(?:ão|ões)\b|bilh(?:ão|ões)\b|reais\b|mentiras\b)", re.I)
SIGLA = re.compile(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,5}\b")  # PT, PL, STF, INSS...
CLASSES = {"NOUN", "PROPN", "VERB", "ADJ"}
# frases-muleta removidas antes da contagem ("deixa eu lhe contar uma coisa")
MULETAS_FRASE = re.compile(
    r"\b(?:deixa eu (?:lhe |te )?(?:contar|falar|dizer|perguntar)|(?:eu )?vou (?:te |lhe )?(?:falar|contar|dizer))"
    r"(?: uma coisa)?(?: (?:pra|para) (?:você|vocês))?|,\s*cara\b", re.I)
# formas que o lematizador do spaCy deixa de reduzir (imperativos no começo da frase)
LEMA_FIX = {"finger": "fingir", "explica": "explicar", "diga": "dizer", "conta": "contar", "fala": "falar", "olha": "olhar"}


def termos(text):
    """Conta os termos de um texto. Ordem: expressões compostas, números com unidade e siglas saem
    primeiro (inteiros); o resto passa pelo spaCy e fica o lema de substantivos, nomes próprios,
    verbos e adjetivos que não sejam palavra vazia nem muleta. Devolve (Counter, nome_proprio)."""
    cont, proprio = Counter(), Counter()
    text = MULETAS_FRASE.sub(" ", text)
    for pat, disp in COMPOSTOS:
        rx = re.compile(rf"\b(?:{pat})\b", re.I)
        cont[disp] += len(rx.findall(text))
        text = rx.sub(" ", text)
    for m in NUMERO.finditer(text):
        num, uni = m.group(1), m.group(2).lower()
        cont[f"{num}%" if uni == "%" else f"{num} {uni}"] += 1
    text = NUMERO.sub(" ", text)
    for m in SIGLA.finditer(text):
        cont[m.group(0)] += 1
    text = SIGLA.sub(" ", text)
    for t in nlp(text):
        palavra = t.is_alpha or ("-" in t.text and all(p.isalpha() for p in t.text.split("-")))
        lema = t.lemma_.lower() if t.pos_ == "NOUN" else LEMA_FIX.get(t.lemma_.lower(), t.lemma_.lower())
        if (palavra and not t.is_stop and len(t) > 2 and lema not in EXTRA_STOP and t.pos_ in CLASSES):
            cont[lema] += 1
            proprio[lema] += t.pos_ == "PROPN"
    return +cont, proprio
