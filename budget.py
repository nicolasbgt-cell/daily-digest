"""Suivi des coûts de l'API : journal usage.log et totaux du mois."""

import sys
from datetime import datetime, timezone
from pathlib import Path

FICHIER_USAGE = Path(__file__).parent / "usage.log"
# Passe à True au premier échec d'écriture : l'avertissement n'est donné qu'une fois.
ecriture_echouee = False

# Prix en dollars par million de tokens.
PRIX_ENTREE_PAR_MILLION = {
    "claude-haiku-4-5-20251001": 1.0,
    "claude-haiku-4-5": 1.0,
    "claude-haiku-5-5": 0.10,
    "claude-sonnet-5-5": 2.0,
}
PRIX_SORTIE_PAR_MILLION = {
    "claude-haiku-4-5-20251001": 5.0,
    "claude-haiku-4-5": 5.0,
    "claude-haiku-5-5": 0.50,
    "claude-sonnet-5-5": 10.0,
}
# Prompts longs : au-delà du seuil de tokens d'entrée, tout l'appel (entrée et
# sortie) est facturé au prix haut. Modèle : (seuil, prix entrée, prix sortie).
PALIERS_PROMPT_LONG = {
    "claude-haiku-5-5": (100_000, 0.50, 2.50),
}


def cout(modele: str, tokens_entree: int, tokens_sortie: int) -> float:
    # Modèle absent du tableau : on applique le prix le plus élevé connu, par prudence.
    prix_entree = PRIX_ENTREE_PAR_MILLION.get(modele, max(PRIX_ENTREE_PAR_MILLION.values()))
    prix_sortie = PRIX_SORTIE_PAR_MILLION.get(modele, max(PRIX_SORTIE_PAR_MILLION.values()))
    if modele in PALIERS_PROMPT_LONG:
        seuil, prix_entree_long, prix_sortie_long = PALIERS_PROMPT_LONG[modele]
        if tokens_entree > seuil:
            prix_entree, prix_sortie = prix_entree_long, prix_sortie_long
    return (tokens_entree * prix_entree + tokens_sortie * prix_sortie) / 1_000_000


def journaliser(etape: str, modele: str, usage, edition: str = "") -> float:
    """Ajoute une ligne à usage.log et renvoie le coût estimé de l'appel.

    Colonnes séparées par des tabulations : date UTC, étape, modèle, tokens
    d'entrée, tokens de sortie, coût en dollars, identifiant d'édition (vide
    hors rédaction). Aucune clé ni donnée de requête n'y figure.
    """
    entree = int(getattr(usage, "input_tokens", 0) or 0)
    sortie = int(getattr(usage, "output_tokens", 0) or 0)
    montant = cout(modele, entree, sortie)
    date = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ligne = f"{date}\t{etape}\t{modele}\t{entree}\t{sortie}\t{montant:.6f}\t{edition}\n"
    try:
        with FICHIER_USAGE.open("a", encoding="utf-8") as f:
            f.write(ligne)
    except OSError:
        # Le journal est un suivi : son échec ne bloque pas le travail, mais se signale.
        global ecriture_echouee
        if not ecriture_echouee:
            ecriture_echouee = True
            print(
                f"Avertissement : impossible d'écrire dans {FICHIER_USAGE.name}, "
                "les coûts ne sont plus enregistrés.",
                file=sys.stderr,
            )
    return montant


def journal_lisible() -> bool:
    """False si usage.log est absent ou illisible (bilan_mois renvoie alors 0)."""
    try:
        FICHIER_USAGE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def bilan_mois() -> tuple[float, int]:
    """Total estimé du mois en cours (UTC) et nombre d'éditions rédigées.

    Fichier absent ou illisible : (0.0, 0). Les lignes abîmées sont ignorées.
    """
    try:
        lignes = FICHIER_USAGE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return 0.0, 0
    maintenant = datetime.now(timezone.utc)
    total = 0.0
    editions = set()
    for ligne in lignes:
        champs = ligne.split("\t")
        if len(champs) < 6:
            continue
        try:
            date = datetime.fromisoformat(champs[0])
            montant = float(champs[5])
        except ValueError:
            continue
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        if (date.year, date.month) != (maintenant.year, maintenant.month):
            continue
        total += montant
        if len(champs) >= 7 and champs[6]:
            editions.add(champs[6])
    return total, len(editions)
