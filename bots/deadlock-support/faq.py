import json
import re
from pathlib import Path


_catalog = json.loads(Path(__file__).with_name("faq_catalog.json").read_text(encoding="utf-8"))
GROUPS = _catalog["groups"]
ARTICLES = {article["id"]: article for article in _catalog["articles"]}
SOURCES = _catalog["sources"]


def normalize(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold().replace("ё", "е")))


SEARCH_ALIASES = {
    key: {
        normalize(alias)
        for alias in (*article["aliases"], article["ru"]["title"], article["en"]["title"])
    }
    for key, article in ARTICLES.items()
}


def find_articles(query):
    normalized = normalize(query[:1200])
    if not normalized:
        return []
    phrase = f" {normalized} "
    matches = []
    for key, aliases in SEARCH_ALIASES.items():
        score = sum(
            1 + 2 * len(alias.split()) + 12 * (alias == normalized)
            for alias in aliases if f" {alias} " in phrase
        )
        if score:
            matches.append((score, key))
    matches.sort(key=lambda match: match[0], reverse=True)
    if len(matches) > 1 and matches[0][0] >= 2 * matches[1][0]:
        matches = matches[:1]
    return [ARTICLES[key] for _, key in matches[:4]]


def title(article, language):
    return article[language]["title"]


def answer(article, language):
    copy = article[language]
    steps = "\n".join(f"{number}. {step}" for number, step in enumerate(copy["steps"], 1))
    return f"{copy['title']}\n\n{steps}"


def links(article, language):
    for key in article["sources"]:
        source = SOURCES[key]
        yield {"text": source[language], "url": source["url"].format(language=language)}
