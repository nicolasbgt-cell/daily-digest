import json
import os
import re
import socket
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

SOURCES = {
    "Hacker News": "https://news.ycombinator.com/rss",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Le Monde": "https://www.lemonde.fr/rss/une.xml",
    "franceinfo": "https://www.franceinfo.fr/titres.rss",
    "Libération": "https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "Le Figaro": "https://www.lefigaro.fr/rss/figaro_actualites.xml",
}
NB_ARTICLES = 15
# Identifiant explicite : certains sites (Le Figaro) bloquent celui de feedparser.
AGENT = "Veille/1.0 (lecteur RSS personnel)"
FICHIER = Path(__file__).parent / "articles.json"
FICHIER_LIENS = Path(__file__).parent / "liens_vus.json"
RETENTION_ARTICLES_HEURES = 24
RETENTION_LIENS_JOURS = 7
# Caractères de contrôle (dont \x1b, début des séquences du terminal).
_CONTROLE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def nettoyer(texte: object) -> str:
    """Texte venu du web, rendu sans danger pour l'affichage dans le terminal."""
    return _CONTROLE.sub(" ", str(texte))


def lire_date(valeur: object) -> datetime | None:
    """Convertit une date ISO 8601 en datetime UTC ; None si elle est invalide."""
    if not isinstance(valeur, str):
        return None
    try:
        date = datetime.fromisoformat(valeur)
    except ValueError:
        return None
    # Une date sans fuseau est considérée comme UTC.
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date


def charger_articles() -> list[dict]:
    """Lit articles.json ; arrête le script s'il est illisible, sans y toucher."""
    if not FICHIER.exists():
        return []
    try:
        articles = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        sys.exit(f"Erreur : {FICHIER.name} est illisible ({e}). Fichier laissé intact.")
    if not isinstance(articles, list) or not all(
        isinstance(a, dict) and "lien" in a for a in articles
    ):
        sys.exit(
            f"Erreur : {FICHIER.name} n'a pas le format attendu "
            "(une liste d'articles avec un champ « lien »). Fichier laissé intact."
        )
    for article in articles:
        if "collecte_le" in article and lire_date(article["collecte_le"]) is None:
            sys.exit(
                f"Erreur : {FICHIER.name} contient une date « collecte_le » invalide "
                f"({article['collecte_le']!r}). Fichier laissé intact."
            )
    return articles


def charger_liens() -> dict[str, str]:
    """Lit liens_vus.json ; arrête le script s'il est illisible, sans y toucher."""
    if not FICHIER_LIENS.exists():
        return {}
    try:
        liens = json.loads(FICHIER_LIENS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        sys.exit(f"Erreur : {FICHIER_LIENS.name} est illisible ({e}). Fichier laissé intact.")
    if not isinstance(liens, dict) or not all(
        isinstance(lien, str) and lire_date(date) is not None
        for lien, date in liens.items()
    ):
        sys.exit(
            f"Erreur : {FICHIER_LIENS.name} n'a pas le format attendu "
            "(un dictionnaire {lien: date ISO 8601}). Fichier laissé intact."
        )
    return liens


def enregistrer(chemin: Path, donnees: list | dict) -> None:
    # Écriture dans un fichier temporaire puis remplacement : si le script
    # plante en cours d'écriture, l'ancien fichier reste intact.
    temporaire = chemin.with_suffix(".json.tmp")
    temporaire.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporaire, chemin)


def lien_web(lien: object) -> bool:
    """Seuls les liens http et https sont acceptés (ni file:, javascript:, ftp:, data:…)."""
    if not isinstance(lien, str):
        return False
    morceaux = urllib.parse.urlsplit(lien.strip())
    return morceaux.scheme in ("http", "https") and bool(morceaux.netloc)


def lire_source(nom: str, url: str) -> list[dict]:
    """Affiche les articles d'une source et les renvoie ; liste vide en cas d'échec."""
    print(f"=== {nom} ===")

    try:
        flux = feedparser.parse(url, agent=AGENT)
    except Exception:
        print(f"Erreur : impossible de lire le flux {nom} ({url})\n")
        return []

    # feedparser ne lève pas d'exception en cas d'échec réseau : il renvoie
    # simplement un flux sans articles, avec l'erreur dans flux.bozo_exception.
    if not flux.entries:
        if flux.get("bozo"):
            print(f"Erreur : flux {nom} inaccessible ou invalide ({url})\n")
        else:
            print(f"Le flux {nom} ne contient aucun article.\n")
        return []

    articles = []
    for entree in flux.entries[:NB_ARTICLES]:
        article = {
            "source": nom,
            "titre": entree.get("title", "(sans titre)"),
            "lien": entree.get("link"),
            "date": entree.get("published") or entree.get("updated"),
            "extrait": entree.get("summary"),
        }
        print(nettoyer(article["titre"]))
        print(nettoyer(article["lien"]) if article["lien"] else "(sans lien)")
        print()
        if article["lien"]:
            articles.append(article)
    return articles


def main() -> None:
    # Chargés avant toute collecte : si un fichier est cassé, on s'arrête tout de suite.
    articles = charger_articles()
    liens_vus = charger_liens()
    maintenant = datetime.now(timezone.utc)
    horodatage = maintenant.isoformat(timespec="seconds")

    # Anciens enregistrements sans date : ils prennent la date du jour.
    for article in articles:
        article.setdefault("collecte_le", horodatage)
    # Liens déjà dans articles.json : notés avant la purge, pour qu'un article
    # purgé ne revienne pas comme nouveau.
    for article in articles:
        liens_vus.setdefault(article["lien"], article["collecte_le"])

    # Purge par durée uniquement, indépendante de l'état des sources.
    limite_articles = maintenant - timedelta(hours=RETENTION_ARTICLES_HEURES)
    avant = len(articles)
    articles = [a for a in articles if lire_date(a["collecte_le"]) > limite_articles]
    purges = avant - len(articles)
    limite_liens = maintenant - timedelta(days=RETENTION_LIENS_JOURS)
    liens_vus = {
        lien: date for lien, date in liens_vus.items() if lire_date(date) > limite_liens
    }

    liens_connus = {a["lien"] for a in articles} | set(liens_vus)

    # feedparser n'a pas de délai d'attente propre : on en fixe un global.
    socket.setdefaulttimeout(15)

    nouveaux = deja_connus = rejetes = 0
    for nom, url in SOURCES.items():
        for article in lire_source(nom, url):
            if not lien_web(article["lien"]):
                rejetes += 1
            elif article["lien"] in liens_connus:
                deja_connus += 1
            else:
                article["collecte_le"] = horodatage
                articles.append(article)
                liens_vus[article["lien"]] = horodatage
                liens_connus.add(article["lien"])
                nouveaux += 1

    # Toujours réécrits : la purge et les dates ajoutées modifient les fichiers
    # même quand aucun article n'est nouveau.
    enregistrer(FICHIER, articles)
    enregistrer(FICHIER_LIENS, liens_vus)

    print(
        f"Bilan : {nouveaux} article(s) nouveau(x), {deja_connus} déjà connu(s), "
        f"{purges} purgé(s), {rejetes} rejeté(s) (lien non http ou https)."
    )


if __name__ == "__main__":
    main()
