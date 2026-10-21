#!/usr/bin/env python3
"""
Synchronisation des documents EcoleDirecte vers un dossier local (partage Samba).

- Range les fichiers par type de document puis par année scolaire :
      /data/EcoleDirecte/<Type>/<AAAA-AAAA>/<date>_<libelle>_<id>.<ext>
- Ne télécharge que les documents absents (suivi via manifest.json, clé = id ED).
- Réutilise le login d'edbot (aucune dépendance externe, 2FA gérée en amont).

Utilisable de deux façons :
  * en ligne de commande :  python3 edsync.py     (login autonome, 2FA mémorisée)
  * depuis le bot Telegram : edsync.run_sync(session_ED_déjà_connectée, log=...)
"""
import os
import re
import sys
import json
import time
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edbot as ed

# --------------------------------------------------------------------------- #
#  Configuration de l'archive
# --------------------------------------------------------------------------- #
ARCHIVE_ROOT = "/data/EcoleDirecte"
MANIFEST_PATH = os.path.join(ARCHIVE_ROOT, "manifest.json")

# Catégorie API -> nom de dossier lisible
CAT_DIRS = {
    "notes": "Bulletins",
    "factures": "Factures",
    "administratifs": "Administratifs",
    "viescolaire": "Vie_scolaire",
}
CATS = list(CAT_DIRS)
PAUSE = 0.4          # pause entre 2 téléchargements (courtoisie anti-abus ED)
MAX_EMPTY_BACK = 1   # nb d'années vides consécutives avant d'arrêter de remonter


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def current_school_year(today=None):
    d = today or datetime.date.today()
    y = d.year if d.month >= 9 else d.year - 1
    return "%d-%d" % (y, y + 1)


def prev_school_year(year):
    a = int(year.split("-")[0])
    return "%d-%d" % (a - 1, a)


def slug(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    s = re.sub(r"[^\w\-.() éèêàâîôûùçÉÈÀ]", "_", s, flags=re.UNICODE)
    return (s[:80] or "document").strip(" ._")


def ext_from(raw, cd):
    m = re.search(r'filename="?[^"]*\.([A-Za-z0-9]{1,5})"?', cd or "")
    if m:
        return "." + m.group(1).lower()
    if raw[:4] == b"%PDF":
        return ".pdf"
    if raw[:2] == b"PK":
        return ".docx"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".bin"


def load_manifest():
    try:
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_manifest(m):
    os.makedirs(ARCHIVE_ROOT, exist_ok=True)
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MANIFEST_PATH)


# --------------------------------------------------------------------------- #
#  Accès API documents
# --------------------------------------------------------------------------- #
def fetch_docs(sess, year, current_year):
    arch = "" if year == current_year else year
    body = sess.post(
        "/v3/familledocuments.awp?verbe=get&archive=%s&v=%s" % (arch, ed.ED_VERSION),
        {}, token=sess.token)
    sess.token = body.get("token") or sess.token
    return body.get("data", {}) if body.get("code") == 200 else {}


def download_file(sess, fid, year, current_year):
    q = "verbe=get&v=%s&fichierId=%s" % (ed.ED_VERSION, fid)
    if year != current_year:
        q += "&archive=true&anneeArchive=" + year
    req = urllib.request.Request(
        ed.ED_BASE + "/v3/telechargement.awp?" + q,
        data=b'data={"forceDownload":0}', method="POST")
    for k, v in (("User-Agent", ed.UA), ("Content-Type", "text/plain"),
                 ("X-Requested-With", "XMLHttpRequest"),
                 ("Origin", "https://www.ecoledirecte.com"),
                 ("Referer", "https://www.ecoledirecte.com/"),
                 ("X-Token", sess.token or "")):
        req.add_header(k, v)
    resp = sess.opener.open(req, timeout=90)
    return resp.read(), resp.headers.get("Content-Disposition", "")


# --------------------------------------------------------------------------- #
#  Synchronisation
# --------------------------------------------------------------------------- #
def run_sync(sess, accounts, log=print):
    """
    `sess` : EDSession déjà authentifiée (sess.token valide).
    `accounts` : la valeur renvoyée par ensure_logged_in()/submit_2fa() lors
    de CETTE connexion — sert à retrouver le prénom de chaque enfant à partir
    de son `idEleve` (chaque document en porte un, vérifié le 03/09/2026),
    pour ranger les documents par enfant. Un second enfant au collège
    mélangerait sinon tout sous un seul dossier.
    Retourne un dict :
      {"new": [ {annee,categorie,date,libelle,fichier} ... ],
       "existing": int, "errors": [...], "inscriptions": int,
       "total": int (docs archivés au total)}
    """
    id_to_prenom = {str(el.get("id")): el.get("prenom") or "Famille"
                    for el in ed.eleves_from_accounts(accounts)}
    manifest = load_manifest()
    current = current_school_year()

    result = {"new": [], "existing": 0, "errors": [], "inscriptions": 0}
    empty_streak = 0
    year = current
    while True:
        data = fetch_docs(sess, year, current)
        n_year = sum(len(data.get(c) or []) for c in CATS)
        result["inscriptions"] += len(data.get("inscriptions") or [])

        if year != current and n_year == 0:
            empty_streak += 1
            if empty_streak > MAX_EMPTY_BACK:
                break
            year = prev_school_year(year)
            continue
        empty_streak = 0

        for cat in CATS:
            for doc in (data.get(cat) or []):
                fid = doc.get("id")
                key = str(fid)
                rec = manifest.get(key)
                if rec and os.path.exists(rec.get("path", "")):
                    result["existing"] += 1
                    continue
                try:
                    raw, cd = download_file(sess, fid, year, current)
                except Exception as e:
                    result["errors"].append("%s/%s id=%s : %s" % (year, cat, fid, e))
                    continue
                if not raw or raw[:1] in (b"{", b"<"):
                    result["errors"].append(
                        "%s/%s id=%s : réponse non-fichier" % (year, cat, fid))
                    continue
                ext = ext_from(raw, cd)
                name = "%s_%s_%s%s" % (doc.get("date") or "sansdate",
                                       slug(doc.get("libelle", "")), fid, ext)
                prenom = id_to_prenom.get(str(doc.get("idEleve")), "Famille")
                dest_dir = os.path.join(ARCHIVE_ROOT, slug(prenom), CAT_DIRS[cat], year)
                os.makedirs(dest_dir, exist_ok=True)
                path = os.path.join(dest_dir, name)
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, path)
                manifest[key] = {
                    "annee": year, "categorie": cat, "date": doc.get("date"),
                    "libelle": doc.get("libelle"), "octets": len(raw),
                    "eleve": prenom, "path": path}
                rel = os.path.relpath(path, ARCHIVE_ROOT)
                result["new"].append({"annee": year, "categorie": cat,
                                      "date": doc.get("date"), "eleve": prenom,
                                      "libelle": doc.get("libelle"), "fichier": rel})
                log("+ %s" % rel)
                time.sleep(PAUSE)

        year = prev_school_year(year)

    save_manifest(manifest)
    result["total"] = len(manifest)
    return result


# --------------------------------------------------------------------------- #
#  Mode standalone
# --------------------------------------------------------------------------- #
def _standalone():
    cfg = ed.load_config()
    state = ed.load_state()
    sess = ed.EDSession(cfg["ecoledirecte"]["identifiant"],
                        cfg["ecoledirecte"]["motdepasse"], state)
    status, payload = sess.ensure_logged_in()
    if status != "ok":
        sys.exit("Login impossible (%s) : %s" % (status, payload))
    ed.save_state(state)  # persiste d'éventuels cn/cv/2FA
    res = run_sync(sess, payload, log=print)
    print("\n=== BILAN SYNC ===")
    print("Nouveaux documents :", len(res["new"]))
    print("Déjà présents      :", res["existing"])
    print("Total archivé      :", res["total"])
    print("Docs inscription (non téléchargeables) :", res["inscriptions"])
    if res["errors"]:
        print("Erreurs :")
        for e in res["errors"]:
            print("  -", e)


if __name__ == "__main__":
    _standalone()
