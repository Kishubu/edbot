#!/usr/bin/env python3
"""
Transfert de la messagerie EcoleDirecte (compte parent, type « Famille »
côté API) vers une boîte mail.

- Chaque nouveau message reçu est reforgé en mail (sujet, corps HTML d'origine,
  pièces jointes) et envoyé en SMTP (Gmail) ; l'expéditeur EcoleDirecte est
  rappelé en tête du corps.
- Ouvrir un message via l'API le passe en « lu » côté EcoleDirecte.
- « Nouveau » = identifiant jamais vu, PAS le drapeau « lu » : un message déjà
  ouvert dans l'app avant le passage du poller doit quand même être transféré.
  Au premier passage, les messages existants sont seulement mémorisés (pas de
  rafale de tout l'historique).
- Un identifiant n'est mémorisé qu'APRÈS l'envoi réussi : un échec SMTP est
  retenté au passage suivant.

Config (config.ini, section [mail]) :
    smtp_user = adresse Gmail expéditrice
    smtp_password = mot de passe d'application
    destinataire = adresse(s) de réception, séparées par des virgules
"""
import re
import ssl
import html
import base64
import time
import smtplib
import datetime
import urllib.request
import email.utils
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import edbot as ed

PARIS = ZoneInfo("Europe/Paris")
# Gmail refuse au-delà de 25 Mo encodés (base64 = +33 %) : au-delà de ce
# budget brut, les pièces jointes restantes sont signalées dans le corps
# au lieu d'être jointes.
MAX_ATTACH_BYTES = 17 * 1024 * 1024
ROLES = {"A": "Administration", "P": "Professeur", "E": "Élève", "F": "Famille"}
PAUSE = 0.4


# --------------------------------------------------------------------------- #
#  API messagerie
# --------------------------------------------------------------------------- #
def famille_id(accounts):
    acc = (accounts or [{}])[0]
    return acc.get("id") if acc.get("typeCompte") in ("1", "2") else None


def list_received(sess, fid, year):
    p = (f"/v3/familles/{fid}/messages.awp?force=false&typeRecuperation=received"
         f"&idClasseur=0&orderBy=date&order=desc&query=&onlyRead=&page=0"
         f"&itemsPerPage=100&getAll=0&verbe=get&v={ed.ED_VERSION}")
    body = sess.post(p, {"anneeMessages": year}, token=sess.token)
    sess.token = body.get("token") or sess.token
    if body.get("code") != 200:
        raise RuntimeError(f"liste des messages : code {body.get('code')}")
    return ((body.get("data") or {}).get("messages") or {}).get("received") or []


def get_message(sess, fid, mid, year):
    body = sess.post(
        f"/v3/familles/{fid}/messages/{mid}.awp?verbe=get&mode=destinataire"
        f"&v={ed.ED_VERSION}", {"anneeMessages": year}, token=sess.token)
    sess.token = body.get("token") or sess.token
    if body.get("code") != 200:
        raise RuntimeError(f"message {mid} : code {body.get('code')}")
    return body.get("data") or {}


def download_attachment(sess, f, year):
    q = (f"verbe=get&v={ed.ED_VERSION}&fichierId={f['id']}"
         f"&leTypeDeFichier={f.get('type') or 'PIECE_JOINTE'}&anneeMessages={year}")
    req = urllib.request.Request(ed.ED_BASE + "/v3/telechargement.awp?" + q,
                                 data=b'data={"forceDownload":0}', method="POST")
    for k, v in (("User-Agent", ed.UA), ("Content-Type", "text/plain"),
                 ("X-Requested-With", "XMLHttpRequest"),
                 ("Origin", "https://www.ecoledirecte.com"),
                 ("Referer", "https://www.ecoledirecte.com/"),
                 ("X-Token", sess.token or "")):
        req.add_header(k, v)
    raw = sess.opener.open(req, timeout=90).read()
    if not raw or raw[:1] in (b"{", b"<"):
        raise RuntimeError("réponse non-fichier")
    return raw


# --------------------------------------------------------------------------- #
#  Forge du mail
# --------------------------------------------------------------------------- #
def sender_label(frm):
    frm = frm or {}
    name = " ".join(x for x in (frm.get("civilite"), frm.get("prenom"),
                                frm.get("particule"), frm.get("nom")) if x)
    role = frm.get("fonctionPersonnel") or ROLES.get(frm.get("role"), "")
    return f"{name} ({role})" if role else (name or "Expéditeur inconnu")


def html_to_text(s):
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "  - ", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


# Images collées dans le corps par l'éditeur EcoleDirecte : data:image/…;base64.
# Gmail ne les affiche pas, et un HTML de plusieurs Mo est tronqué à 102 Ko
# (« Message tronqué ») : on les sort en images intégrées (cid:).
DATA_IMG = re.compile(
    r"""(src\s*=\s*["'])data:image/([a-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)(["'])""",
    re.I)


def extract_inline_images(content):
    """-> (html avec des cid:, [(cid, sous-type, octets)])"""
    images = []

    def repl(mo):
        try:
            raw = base64.b64decode(mo.group(3))
        except ValueError:
            return mo.group(0)
        cid = email.utils.make_msgid("edimg")
        images.append((cid, mo.group(2).lower(), raw))
        return f"{mo.group(1)}cid:{cid[1:-1]}{mo.group(4)}"

    return DATA_IMG.sub(repl, content), images


def build_mail(msg, attachments, skipped, mail_from, to, content=None,
               inline=()):
    sender = sender_label(msg.get("from"))
    try:
        dt = datetime.datetime.strptime(msg.get("date", ""), "%Y-%m-%d %H:%M:%S") \
            .replace(tzinfo=PARIS)
    except ValueError:
        dt = datetime.datetime.now(PARIS)
    quand = dt.strftime("%d/%m/%Y à %H:%M")
    if content is None:
        content = ed._b64d(msg.get("content", ""))

    note = ""
    if skipped:
        note = ("<p><i>Pièce(s) jointe(s) non transférée(s), à récupérer sur "
                "EcoleDirecte : " + html.escape(", ".join(skipped)) + "</i></p>")
    head = ('<div style="border-left:3px solid #1a73e8;padding:6px 10px;'
            'margin-bottom:14px;background:#f1f5fb;font-family:sans-serif">'
            f"<b>De :</b> {html.escape(sender)}<br>"
            f"<b>Reçu sur EcoleDirecte le :</b> {quand}</div>")

    m = EmailMessage()
    m["Subject"] = "[EcoleDirecte] " + (msg.get("subject") or "(sans objet)")
    m["From"] = email.utils.formataddr((f"{sender} via EcoleDirecte", mail_from))
    m["To"] = ", ".join(to)
    m["Date"] = email.utils.format_datetime(dt)
    m["Message-ID"] = email.utils.make_msgid(f"ed{msg.get('id')}")
    text = f"De : {sender}\nReçu sur EcoleDirecte le : {quand}\n\n{html_to_text(content)}"
    if skipped:
        text += "\n\n(Pièce(s) jointe(s) non transférée(s) : " + ", ".join(skipped) + ")"
    m.set_content(text)
    m.add_alternative(f"<html><body>{head}{note}{content}</body></html>",
                      subtype="html")
    if inline:
        html_part = m.get_payload()[1]
        for cid, sub, raw in inline:
            html_part.add_related(raw, maintype="image", subtype=sub, cid=cid,
                                  disposition="inline")
    for name, raw in attachments:
        maintype, subtype = _mime(name, raw)
        m.add_attachment(raw, maintype=maintype, subtype=subtype, filename=name)
    return m


def _mime(name, raw):
    if raw[:4] == b"%PDF" or name.lower().endswith(".pdf"):
        return "application", "pdf"
    if raw[:3] == b"\xff\xd8\xff":
        return "image", "jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "png"
    return "application", "octet-stream"


def send_mail(cfg, m):
    user = cfg["smtp_user"].strip()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, cfg["smtp_password"].strip())
        smtp.send_message(m)


# --------------------------------------------------------------------------- #
#  Transfert
# --------------------------------------------------------------------------- #
def forward_one(sess, fid, mid, year, mcfg):
    msg = get_message(sess, fid, mid, year)   # passe le message en « lu »
    content, inline = extract_inline_images(ed._b64d(msg.get("content", "")))
    attachments, skipped = [], []
    budget = MAX_ATTACH_BYTES - sum(len(r) for _, _, r in inline)
    for f in msg.get("files") or []:
        name = f.get("libelle") or f"piece_jointe_{f.get('id')}"
        try:
            raw = download_attachment(sess, f, year)
        except Exception:
            skipped.append(name)
            continue
        if len(raw) > budget:
            skipped.append(name)
            continue
        budget -= len(raw)
        attachments.append((name, raw))
        time.sleep(PAUSE)
    to = [a.strip() for a in mcfg["destinataire"].split(",") if a.strip()]
    try:
        send_mail(mcfg, build_mail(msg, attachments, skipped,
                                   mcfg["smtp_user"].strip(), to,
                                   content=content, inline=inline))
    except Exception as e:
        raise MailSendError(e) from e
    return msg.get("subject") or ""


class MailSendError(Exception):
    """Échec de l'envoi du mail (SMTP) — notre côté, à distinguer d'un
    EcoleDirecte qui ne répond pas."""


def run_forward(sess, accounts, state, mcfg, log=print):
    """Transfère les messages reçus jamais vus. Met à jour et sauvegarde
    `state` après chaque envoi réussi. Retourne
    {"forwarded": [sujets], "errors": [envoi SMTP], "ed_errors": [lecture
    EcoleDirecte], "initialized": bool}."""
    res = {"forwarded": [], "errors": [], "ed_errors": [], "initialized": False}
    fid = famille_id(accounts)
    if not fid:
        return res
    import edsync
    year = edsync.current_school_year()
    received = list_received(sess, fid, year)
    first_run = "mail_seen" not in state
    seen = set(state.get("mail_seen") or [])
    if first_run:
        state["mail_seen"] = sorted(m["id"] for m in received)
        ed.save_state(state)
        res["initialized"] = True
        return res
    for m in sorted(received, key=lambda m: m.get("date", "")):
        mid = m["id"]
        if mid in seen:
            continue
        try:
            res["forwarded"].append(forward_one(sess, fid, mid, year, mcfg))
        except Exception as e:
            key = "errors" if isinstance(e, MailSendError) else "ed_errors"
            res[key].append(f"message {mid} ({m.get('subject')}) : {e}")
            continue
        seen.add(mid)
        state["mail_seen"] = sorted(seen)
        ed.save_state(state)  # tout de suite : un plantage ne doit pas renvoyer
        log(f"transféré : {mid} {m.get('subject')}")
    return res
