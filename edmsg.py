#!/usr/bin/env python3


import re
import ssl
import html
import base64
import time
import datetime
import urllib.request
import email.utils
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import edbot as ed

# Gmail refuse au-delà de 25 Mo encodés (base64 = +33 %) : au-delà de ce
# budget brut, les pièces jointes restantes sont signalées dans le corps
# au lieu d'être jointes.


# --------------------------------------------------------------------------- #
#  API messagerie
# --------------------------------------------------------------------------- #
def famille_id(accounts):
    pass


def list_received(sess, fid, year):
    pass


def get_message(sess, fid, mid, year):
    pass


def download_attachment(sess, f, year):
    pass


# --------------------------------------------------------------------------- #
#  Forge du mail
# --------------------------------------------------------------------------- #
def sender_label(frm):
    pass


def html_to_text(s):
    pass


# Images collées dans le corps par l'éditeur EcoleDirecte : data:image/…;base64.
# Gmail ne les affiche pas, et un HTML de plusieurs Mo est tronqué à 102 Ko
# (« Message tronqué ») : on les sort en images intégrées (cid:).












# --------------------------------------------------------------------------- #
#  Transfert
# --------------------------------------------------------------------------- #
