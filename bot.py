import asyncio
import json
import logging
import os
import secrets
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.error import InvalidToken, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import budget
import redaction

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s : %(message)s", level=logging.INFO
)
# httpx journalise les URL complètes, qui contiennent le token : on le fait taire.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

PARIS = ZoneInfo("Europe/Paris")
# Envoi automatique de la liste ; regrouper.py doit avoir tourné avant (planifié à part).
HEURE_LISTE_AUTO = time(17, 55, tzinfo=PARIS)
FICHIER_SUJETS = Path(__file__).parent / "sujets.json"
NB_SUJETS_LISTE = 20
MAX_CHOIX = 5
TAILLE_MESSAGE = 4000  # limite Telegram : 4096 caractères, on garde de la marge
NB_LISTES_GARDEES = 10  # listes figées conservées en mémoire
SANS_APERCU = LinkPreviewOptions(is_disabled=True)
PLAFOND_MENSUEL_USD = 9
DELAI_REDACTION = 240  # secondes ; au-delà, plus aucun appel à l'API n'est lancé
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


class MasqueSecrets(logging.Filter):
    """Remplace par *** la valeur exacte des secrets dans les messages et les traces."""

    def __init__(self, secrets_: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets_ if s]

    def masquer(self, texte: str) -> str:
        for secret in self.secrets:
            texte = texte.replace(secret, "***")
        return texte

    def filter(self, record: logging.LogRecord) -> bool:
        # Message déjà assemblé (les arguments peuvent contenir le secret).
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)  # arguments mal formés : le gabarit seul
        record.msg = self.masquer(message)
        record.args = None
        # La trace est mise en forme ici, masquée, puis gardée en texte seul.
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = self.masquer(record.exc_text)
        if record.stack_info:
            record.stack_info = self.masquer(record.stack_info)
        return True


def sans_trace_token_refuse(record: logging.LogRecord) -> bool:
    """La bibliothèque journalise la trace d'InvalidToken avant de la relever ;
    main() la remplace par un message clair."""
    exception = record.exc_info[1] if record.exc_info else None
    return not isinstance(exception, InvalidToken)


def masquer_secrets_dans_les_journaux() -> None:
    """Pose les filtres sur tous les handlers : ils voient aussi les messages des
    bibliothèques (telegram, httpx…), qui remontent jusqu'à ces handlers."""
    filtre = MasqueSecrets([
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        os.getenv("ANTHROPIC_API_KEY", "").strip(),
    ])
    for handler in logging.getLogger().handlers:
        # Dans cet ordre : MasqueSecrets remplace la trace par du texte.
        handler.addFilter(sans_trace_token_refuse)
        handler.addFilter(filtre)


def suivi_du_plafond_perdu(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Des éditions ont coûté dans cette session mais le journal ne les reflète pas."""
    return context.bot_data.get("editions_redigees", 0) > 0 and (
        not budget.journal_lisible() or budget.ecriture_echouee
    )


AVERTISSEMENT_PLAFOND = (
    "Attention : usage.log est illisible ou n'a pas pu être écrit. "
    "Le plafond mensuel n'est plus suivi."
)


def dollars(montant: float) -> str:
    return f"{montant:.4f}".replace(".", ",") + " $"


class SujetsIndisponibles(Exception):
    """sujets.json absent, illisible ou vide ; le message est destiné à l'utilisateur."""


def lire_sujets() -> list[dict]:
    """Lit sujets.json en lecture seule et renvoie les premiers sujets valides."""
    nom = FICHIER_SUJETS.name  # jamais le chemin complet dans les messages
    if not FICHIER_SUJETS.exists():
        raise SujetsIndisponibles(f"Le fichier {nom} est introuvable. Lance regrouper.py.")
    try:
        sujets = json.loads(FICHIER_SUJETS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SujetsIndisponibles(f"Le fichier {nom} est illisible. Relance regrouper.py.")
    if not isinstance(sujets, list) or not all(
        isinstance(s, dict)
        and isinstance(s.get("titre"), str)
        and isinstance(s.get("articles"), list)
        and all(isinstance(a, dict) for a in s["articles"])
        for s in sujets
    ):
        raise SujetsIndisponibles(f"Le fichier {nom} n'a pas le format attendu.")
    if not sujets:
        raise SujetsIndisponibles(f"Le fichier {nom} ne contient aucun sujet.")
    return sujets[:NB_SUJETS_LISTE]


def noms_des_sources(sujet: dict) -> str:
    """Noms des sources du sujet, sans doublon, dans l'ordre des articles."""
    noms = dict.fromkeys(
        a["source"] for a in sujet["articles"]
        if isinstance(a.get("source"), str) and a["source"].strip()
    )
    return ", ".join(noms) or "source inconnue"


def decouper(lignes: list[str]) -> list[str]:
    """Assemble des lignes en messages de TAILLE_MESSAGE caractères au plus."""
    messages, courant = [], ""
    for ligne in lignes:
        # Une ligne seule trop longue est coupée en morceaux.
        while len(ligne) > TAILLE_MESSAGE:
            if courant:
                messages.append(courant)
                courant = ""
            messages.append(ligne[:TAILLE_MESSAGE])
            ligne = ligne[TAILLE_MESSAGE:]
        if courant and len(courant) + 1 + len(ligne) > TAILLE_MESSAGE:
            messages.append(courant)
            courant = ligne
        else:
            courant = f"{courant}\n{ligne}" if courant else ligne
    if courant:
        messages.append(courant)
    return messages


def clavier(id_liste: str, nb_sujets: int, coches: set[int]) -> InlineKeyboardMarkup:
    # callback_data ne contient que des codes courts : c=cocher, v=valider, a=annuler.
    numeros = [
        InlineKeyboardButton(
            f"✅ {n}" if n in coches else str(n), callback_data=f"c:{id_liste}:{n}"
        )
        for n in range(1, nb_sujets + 1)
    ]
    lignes = [numeros[i : i + 5] for i in range(0, len(numeros), 5)]
    lignes.append(
        [
            InlineKeyboardButton("Valider", callback_data=f"v:{id_liste}"),
            InlineKeyboardButton("Annuler", callback_data=f"a:{id_liste}"),
        ]
    )
    return InlineKeyboardMarkup(lignes)


async def log_unknown_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mode découverte : affiche l'identifiant de chaque expéditeur, sans répondre."""
    user = update.effective_user
    if user:
        print(f"Message reçu de {user.full_name} (@{user.username}) : id = {user.id}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Bot opérationnel")


def avertissement_anciennete() -> str | None:
    """Avertissement si sujets.json n'a pas été modifié aujourd'hui (heure de Paris)."""
    try:
        modifie = datetime.fromtimestamp(FICHIER_SUJETS.stat().st_mtime, PARIS).date()
    except OSError:
        return None  # le fichier vient d'être lu ; rien de plus à signaler
    aujourd_hui = datetime.now(PARIS).date()
    if modifie >= aujourd_hui:
        return None
    if modifie == aujourd_hui - timedelta(days=1):
        quand = "d'hier"
    else:
        quand = f"du {modifie.day} {MOIS[modifie.month - 1]}"
    return f"Attention : les sujets datent {quand} (le regroupement n'a peut-être pas tourné)."


async def envoyer_liste(envoyer, bot_data: dict) -> None:
    """Envoie la liste des sujets avec ses boutons ; utilisée par /liste et par
    l'envoi automatique. `envoyer(texte, **options)` envoie un message."""
    try:
        sujets = lire_sujets()
    except SujetsIndisponibles as e:
        await envoyer(str(e))
        return

    # Copie figée des sujets affichés : les boutons y renvoient par numéro,
    # même si sujets.json change entre-temps.
    listes = bot_data.setdefault("listes", {})
    id_liste = secrets.token_hex(4)
    listes[id_liste] = {"sujets": sujets, "coches": set()}
    while len(listes) > NB_LISTES_GARDEES:
        del listes[next(iter(listes))]  # la plus ancienne

    lignes = []
    avertissement = avertissement_anciennete()
    if avertissement:
        lignes += [avertissement, ""]
    lignes += ["Sujets du moment :", ""]
    for n, sujet in enumerate(sujets, start=1):
        nb_articles = len(sujet["articles"])
        articles = "article" if nb_articles <= 1 else "articles"
        lignes.append(
            f"{n}. {sujet['titre']} ({nb_articles} {articles}, {noms_des_sources(sujet)})"
        )
    lignes += ["", f"Coche jusqu'à {MAX_CHOIX} sujets puis valide."]

    # Texte brut (pas de parse_mode) ; le clavier va sous le dernier morceau.
    morceaux = decouper(lignes)
    for morceau in morceaux[:-1]:
        await envoyer(morceau)
    await envoyer(morceaux[-1], reply_markup=clavier(id_liste, len(sujets), set()))


async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await envoyer_liste(update.message.reply_text, context.bot_data)


async def liste_automatique(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tâche quotidienne : la liste part vers l'utilisateur autorisé uniquement."""
    user_id = context.bot_data["user_id"]

    async def envoyer(texte: str, **options) -> None:
        await context.bot.send_message(chat_id=user_id, text=texte, **options)

    try:
        await envoyer_liste(envoyer, context.bot_data)
    except Exception as e:
        # Seul le type d'erreur est consigné : ni trace, ni clé, ni token.
        # Le bot continue ; la tâche repartira demain.
        log.error("Envoi automatique de la liste échoué : %s", type(e).__name__)


def autorise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return update.callback_query.from_user.id == context.bot_data["user_id"]


async def liste_expiree(update: Update) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Cette liste a expiré, tape /liste")


async def bouton_cocher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update, context):
        return  # utilisateur non autorisé : ignoré silencieusement
    query = update.callback_query
    _, id_liste, numero = query.data.split(":")
    etat = context.bot_data.get("listes", {}).get(id_liste)
    if etat is None:
        await liste_expiree(update)
        return
    n = int(numero)
    if not 1 <= n <= len(etat["sujets"]):
        await liste_expiree(update)
        return
    coches = etat["coches"]
    if n in coches:
        coches.remove(n)
    elif len(coches) >= MAX_CHOIX:
        await query.answer(f"{MAX_CHOIX} sujets maximum")
        return
    else:
        coches.add(n)
    await query.answer()
    # Modifie le clavier existant, sans nouveau message.
    await query.edit_message_reply_markup(
        reply_markup=clavier(id_liste, len(etat["sujets"]), coches)
    )


async def envoyer_liens(message, etat: dict) -> None:
    """Titres et liens des sujets cochés, en texte brut et sans aperçu des liens."""
    for n in sorted(etat["coches"]):
        sujet = etat["sujets"][n - 1]
        lignes = [f"{n}. {sujet['titre']}"]
        for article in sujet["articles"]:
            lignes += [
                "",
                f"{article.get('source', '?')} : {article.get('titre', '(sans titre)')}",
                str(article.get("lien", "(sans lien)")),
            ]
        for morceau in decouper(lignes):
            await message.reply_text(morceau, link_preview_options=SANS_APERCU)


def numeros_non_rediges(choix: dict, non_rediges: list[tuple[dict, str]]) -> list[tuple[int, str]]:
    """(numéro dans la liste, raison) ; comparaison par identité, les sujets
    renvoyés sont ceux qui ont été transmis."""
    return [(n, raison) for n in sorted(choix["coches"])
            for sujet, raison in non_rediges if choix["sujets"][n - 1] is sujet]


async def signaler_non_rediges(message, choix: dict, non_rediges: list[tuple[dict, str]],
                               aucun_redige: bool) -> None:
    """Nombre de sujets non rédigés, raison de chacun, puis titres et liens."""
    numeros = numeros_non_rediges(choix, non_rediges)
    if aucun_redige:
        entete = "Aucun sujet n'a été rédigé, il n'y a pas d'édition."
    else:
        entete = f"{len(numeros)} sujet(s) non rédigé(s)."
    lignes = [entete, ""]
    lignes += [f"{n}. {choix['sujets'][n - 1]['titre']} : {raison}" for n, raison in numeros]
    lignes += ["", "Voici les titres et les liens."]
    for morceau in decouper(lignes):
        await message.reply_text(morceau)
    await envoyer_liens(message, {"sujets": choix["sujets"], "coches": {n for n, _ in numeros}})


async def bouton_valider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update, context):
        return  # utilisateur non autorisé : ignoré silencieusement
    query = update.callback_query
    id_liste = query.data.split(":")[1]
    etat = context.bot_data.get("listes", {}).get(id_liste)
    if etat is None:
        await liste_expiree(update)
        return
    if etat.get("validee"):
        await query.answer("Cette liste a déjà été validée")
        return
    if not etat["coches"]:
        await query.answer("Aucun sujet coché")
        return
    if context.bot_data.get("redaction_en_cours"):
        await query.answer("Une rédaction est déjà en cours")
        return
    # Mises à jour traitées en parallèle : les deux drapeaux sont posés avant
    # tout await, pour qu'un second clic ne puisse pas passer entre les deux.
    etat["validee"] = True
    context.bot_data["redaction_en_cours"] = True
    # Copie de la sélection : elle ne bouge plus, même si on coche encore.
    choix = {"sujets": etat["sujets"], "coches": set(etat["coches"])}
    sujets = [choix["sujets"][n - 1] for n in sorted(choix["coches"])]

    def rediger() -> redaction.Edition:
        try:
            return redaction.rediger_edition(sujets, delai_max=DELAI_REDACTION)
        finally:
            # Libéré par le fil lui-même, une fois réellement terminé.
            context.bot_data["redaction_en_cours"] = False

    try:
        await query.answer()
        if suivi_du_plafond_perdu(context):
            await query.message.reply_text(AVERTISSEMENT_PLAFOND)
        total, _ = budget.bilan_mois()
        if total > PLAFOND_MENSUEL_USD:
            context.bot_data["redaction_en_cours"] = False  # aucun fil lancé
            await query.message.reply_text(
                f"Plafond mensuel atteint : {dollars(total)} estimés ce mois-ci "
                f"pour un plafond de {PLAFOND_MENSUEL_USD} $. Pas de rédaction ; "
                "voici les titres et les liens."
            )
            await envoyer_liens(query.message, choix)
            return
        await query.message.reply_text("Rédaction de l'édition en cours, 1 à 3 minutes…")
    except BaseException:
        context.bot_data["redaction_en_cours"] = False  # aucun fil lancé
        raise

    try:
        # Dans un fil à part ; grâce à concurrent_updates, le bot continue de
        # répondre pendant la rédaction.
        edition = await asyncio.to_thread(rediger)
    except redaction.AucunSujetRedige as e:
        if e.cout > 0:  # des appels ont été payés
            context.bot_data["editions_redigees"] = context.bot_data.get("editions_redigees", 0) + 1
        await signaler_non_rediges(query.message, choix, e.non_rediges, aucun_redige=True)
        return
    except redaction.RedactionErreur as e:
        await query.message.reply_text(f"La rédaction a échoué : {e}\nVoici les titres et les liens.")
        await envoyer_liens(query.message, choix)
        return
    except Exception as e:
        # Seul le type d'erreur est consigné : ni trace, ni clé, ni token.
        log.error("Rédaction échouée : %s", type(e).__name__)
        await query.message.reply_text(
            "La rédaction a échoué (erreur inattendue). Voici les titres et les liens."
        )
        await envoyer_liens(query.message, choix)
        return
    # Le coût est engagé : sert à détecter un journal devenu illisible.
    context.bot_data["editions_redigees"] = context.bot_data.get("editions_redigees", 0) + 1

    # Le PDF part uniquement vers l'utilisateur autorisé.
    user_id = context.bot_data["user_id"]
    try:
        with edition.chemin.open("rb") as pdf:
            await context.bot.send_document(
                chat_id=user_id, document=pdf, filename=edition.chemin.name
            )
    except (OSError, TelegramError) as e:
        log.error("Envoi du PDF échoué : %s", type(e).__name__)
        await query.message.reply_text(
            f"L'édition est rédigée ({edition.chemin.name}) mais l'envoi du PDF a échoué."
        )
    await context.bot.send_message(
        chat_id=user_id, text=f"Coût estimé de cette édition : {dollars(edition.cout)}"
    )
    if edition.non_rediges:
        await signaler_non_rediges(query.message, choix, edition.non_rediges, aucun_redige=False)


async def cout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    total, editions = budget.bilan_mois()
    maintenant = datetime.now(timezone.utc)
    await update.message.reply_text(
        f"{MOIS[maintenant.month - 1].capitalize()} {maintenant.year} : "
        f"{dollars(total)} estimés, {editions} édition(s).\n"
        f"Plafond mensuel : {PLAFOND_MENSUEL_USD} $."
    )
    if suivi_du_plafond_perdu(context):
        await update.message.reply_text(AVERTISSEMENT_PLAFOND)


async def bouton_annuler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update, context):
        return  # utilisateur non autorisé : ignoré silencieusement
    query = update.callback_query
    id_liste = query.data.split(":")[1]
    context.bot_data.get("listes", {}).pop(id_liste, None)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Sélection annulée")


async def bouton_inconnu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Boutons d'une ancienne version du bot ou de forme inattendue."""
    if not autorise(update, context):
        return  # utilisateur non autorisé : ignoré silencieusement
    await liste_expiree(update)


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(update.message.text)


def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")
    masquer_secrets_dans_les_journaux()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("Erreur : la variable TELEGRAM_BOT_TOKEN est absente ou vide dans .env")

    user_id_raw = os.getenv("TELEGRAM_USER_ID", "").strip()

    # Sans concurrent_updates, la bibliothèque attend la fin de chaque mise à jour
    # avant de lire la suivante : le bot resterait muet pendant une rédaction.
    # Chaque handler vérifie déjà l'identifiant de l'expéditeur.
    app = Application.builder().token(token).concurrent_updates(True).build()

    if not user_id_raw:
        print(
            "Attention : la variable TELEGRAM_USER_ID est absente ou vide dans .env.\n"
            "Mode découverte : le bot ne répond à personne et affiche l'identifiant "
            "de chaque expéditeur. Écris-lui un message pour récupérer le tien."
        )
        app.add_handler(MessageHandler(filters.ALL, log_unknown_user))
    else:
        try:
            user_id = int(user_id_raw)
        except ValueError:
            sys.exit("Erreur : TELEGRAM_USER_ID doit être un nombre entier.")

        # Seul l'utilisateur autorisé passe ; les autres sont ignorés silencieusement.
        me = filters.User(user_id=user_id)
        app.add_handler(CommandHandler("start", start, filters=me))
        app.add_handler(CommandHandler("liste", liste, filters=me))
        app.add_handler(CommandHandler("cout", cout, filters=me))
        app.bot_data["user_id"] = user_id
        app.add_handler(CallbackQueryHandler(bouton_cocher, pattern=r"^c:[0-9a-f]+:\d+$"))
        app.add_handler(CallbackQueryHandler(bouton_valider, pattern=r"^v:[0-9a-f]+$"))
        app.add_handler(CallbackQueryHandler(bouton_annuler, pattern=r"^a:[0-9a-f]+$"))
        app.add_handler(CallbackQueryHandler(bouton_inconnu))
        app.add_handler(MessageHandler(me & filters.TEXT & ~filters.COMMAND, echo))
        # Fuseau porté par HEURE_LISTE_AUTO : fuseau fixe, quelle que soit la machine.
        app.job_queue.run_daily(liste_automatique, time=HEURE_LISTE_AUTO,
                                name="liste_automatique")
        log.info("Bot démarré pour l'utilisateur autorisé.")

    # run_polling gère Ctrl+C (SIGINT) et arrête proprement l'application.
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except InvalidToken:
        # Message rédigé ici : ni token, ni trace.
        sys.exit(
            "Erreur : Telegram refuse le token. Vérifie TELEGRAM_BOT_TOKEN dans .env."
        )
    print("Bot arrêté.")


if __name__ == "__main__":
    main()
