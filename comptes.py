"""Comptes utilisateurs et sessions (2.6.102, multi utilisateur en 2.6.103), dans la base SQLite kamcine.db du dossier des données.

Une installation, plusieurs comptes : chaque compte a son identifiant et son mot de passe, chaque navigateur ou téléphone
sa propre session, et plusieurs sessions d'un même compte coexistent. Le premier compte créé est administrateur ; un
administrateur crée les suivants, change leur rôle, les désactive (leurs sessions sont révoquées) ou réinitialise leur
mot de passe. Il reste toujours au moins un administrateur actif. Chaque compte a son nom affiché et sa photo
(avatars/<id>.jpg du dossier des données, nom du fichier dans la base).

2.6.104 : un Administrateur administre CETTE installation (pas KamCiné en général). Un Utilisateur est une personne
autorisée à utiliser cette installation ; quelques permissions (PERMISSIONS) précisent ce qu'il peut faire, un
administrateur les a toutes. Données personnelles dans la base : favoris (user_favorites) et activité (user_activity :
demandes, séances lancées), toujours liées à users.id. Le reste (catalogue, historique de l'Apple TV, notifications…)
reste commun, dans ses fichiers JSON. users.id est un identifiant local à l'installation : un lien futur vers une
identité KamCiné globale se ferait par une table à part (user_id, fournisseur, sujet), sans toucher à ces tables.

Mots de passe : PBKDF2 HMAC SHA256, sel unique par compte, format versionné pbkdf2_sha256$itérations$sel$empreinte,
comparaison en temps constant. Sessions : jeton aléatoire remis au navigateur, seule son empreinte SHA256 est gardée.

Migrations : numérotées dans MIGRATIONS, appliquées au démarrage dans l'ordre, chacune dans sa transaction, après une
copie de la base dans backups/. Une base plus récente que ce code est refusée (BaseTropRecente) : jamais d'écriture
qui l'abîmerait après un retour à une ancienne version."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time

import chemins

ALGO = "pbkdf2_sha256"
ITERATIONS = 600_000
ROLES = ("admin", "utilisateur")
IDENTIFIANT = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MDP_MIN, MDP_MAX = 8, 256
_verrou = threading.Lock()


class BaseTropRecente(Exception):
    pass


class Refus(Exception):
    """Refus lisible par l'utilisateur (identifiant pris, mot de passe trop court…)."""
    def __init__(self, message, champ=""):
        super().__init__(message)
        self.champ = champ


def chemin_base():
    return chemins.donnee("kamcine.db")


# ---------- Migrations ----------
def _executer(c, script):
    """Une requête à la fois : executescript validerait d'office la transaction de la migration."""
    for requete in script.split(";"):
        if requete.strip():
            c.execute(requete)


def _m1_comptes(c):
    _executer(c, """
    CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, nom TEXT NOT NULL, appliquee_a REAL NOT NULL);
    CREATE TABLE installation (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL, maj_a REAL NOT NULL);
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identifiant TEXT NOT NULL UNIQUE COLLATE NOCASE,
        nom TEXT NOT NULL,
        mot_de_passe TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'utilisateur')),
        cree_a REAL NOT NULL,
        maj_a REAL NOT NULL,
        derniere_connexion REAL,
        desactive_a REAL
    );
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empreinte TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        cree_a REAL NOT NULL,
        expire_a REAL NOT NULL,
        duree_s INTEGER NOT NULL,
        vue_a REAL NOT NULL,
        appareil TEXT NOT NULL DEFAULT '',
        revoquee_a REAL
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    """)


def _m2_avatars(c):
    _executer(c, """
    ALTER TABLE users ADD COLUMN avatar TEXT;
    ALTER TABLE users ADD COLUMN avatar_maj REAL
    """)


# Permissions d'un Utilisateur (2.6.104). Un Administrateur les a toutes, sans ligne en base. Pour en ajouter une :
# l'ajouter ici, décider par une migration si les comptes existants la reçoivent, et la vérifier côté service
# (PERMISSION_ROUTES dans app/main.py).
PERMISSIONS = (
    ("demander", "Demander des médias", "Peut effectuer des demandes depuis le catalogue."),
    ("seances", "Piloter les séances", "Peut lancer et contrôler une séance KamCiné."),
    ("telechargements", "Téléchargements", "Peut consulter les téléchargements et utiliser leurs actions."),
)
NOMS_PERMISSIONS = tuple(p[0] for p in PERMISSIONS)
PERMISSIONS_DEFAUT = NOMS_PERMISSIONS


def _m3_personnel(c):
    _executer(c, """
    CREATE TABLE user_permissions (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission TEXT NOT NULL,
        accordee_a REAL NOT NULL,
        PRIMARY KEY (user_id, permission)
    );
    CREATE TABLE user_favorites (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
        tmdb_id INTEGER NOT NULL,
        titre TEXT NOT NULL DEFAULT '',
        affiche TEXT,
        annee TEXT NOT NULL DEFAULT '',
        cree_a REAL NOT NULL,
        PRIMARY KEY (user_id, media_type, tmdb_id)
    );
    CREATE TABLE user_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        genre TEXT NOT NULL,
        media_type TEXT,
        tmdb_id INTEGER,
        details TEXT NOT NULL DEFAULT '',
        cree_a REAL NOT NULL
    );
    CREATE INDEX user_activity_user ON user_activity(user_id, genre, cree_a);
    ALTER TABLE users ADD COLUMN bienvenue_a REAL
    """)
    maintenant = time.time()
    for (uid,) in c.execute("SELECT id FROM users").fetchall():
        for p in PERMISSIONS_DEFAUT:
            c.execute("INSERT INTO user_permissions VALUES (?, ?, ?)", (uid, p, maintenant))
    # La bienvenue est pour un compte créé par un administrateur : l'administrateur qui utilisait déjà KamCiné ne la voit pas.
    c.execute("UPDATE users SET bienvenue_a = ? WHERE role = 'admin'", (maintenant,))


def _m4_preferences_personnelles(c):
    _executer(c, """
    CREATE TABLE user_preferences (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        cle TEXT NOT NULL,
        valeur TEXT NOT NULL,
        maj_a REAL NOT NULL,
        PRIMARY KEY (user_id, cle)
    )
    """)


MIGRATIONS = [(1, "comptes et sessions", _m1_comptes), (2, "photo de chaque compte", _m2_avatars),
              (3, "permissions, favoris et activité par compte", _m3_personnel),
              (4, "préférences personnelles", _m4_preferences_personnelles)]
VERSION_BASE = MIGRATIONS[-1][0]


def _ouvrir():
    chemin = chemin_base()
    if not os.path.exists(chemin):
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        os.close(os.open(chemin, os.O_WRONLY | os.O_CREAT, 0o600))   # privée dès sa création
    c = sqlite3.connect(chemin, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 10000")
    return c


def migrer():
    """Met la base à jour ; renvoie la version finale. Lève BaseTropRecente si la base vient d'une version plus récente."""
    with _verrou:
        existait = os.path.exists(chemin_base()) and os.path.getsize(chemin_base()) > 0
        c = _ouvrir()
        try:
            c.execute("PRAGMA journal_mode = WAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > VERSION_BASE:
                raise BaseTropRecente("La base kamcine.db vient d'une version plus récente de KamCiné (schéma %d, cette version "
                                      "connaît le schéma %d). Remets la version récente ou restaure une sauvegarde." % (version, VERSION_BASE))
            a_faire = [m for m in MIGRATIONS if m[0] > version]
            if a_faire and existait and version > 0:
                _sauvegarder(c, version)
            for numero, nom, fonction in a_faire:
                c.execute("BEGIN IMMEDIATE")
                try:
                    fonction(c)
                    c.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (numero, nom, time.time()))
                    c.execute("PRAGMA user_version = %d" % numero)
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise
            return c.execute("PRAGMA user_version").fetchone()[0]
        finally:
            c.close()


def _sauvegarder(c, version):
    dossier = chemins.donnee("backups")
    os.makedirs(dossier, exist_ok=True)
    cible = os.path.join(dossier, "kamcine-schema%d-%s.db" % (version, time.strftime("%Y%m%d-%H%M%S")))
    dest = sqlite3.connect(cible)
    try:
        c.backup(dest)
    finally:
        dest.close()
    os.chmod(cible, 0o600)


_base = _ouvrir


# ---------- Mots de passe ----------
def hacher(mot_de_passe, iterations=ITERATIONS):
    sel = secrets.token_bytes(16)
    brut = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), sel, iterations)
    return "%s$%d$%s$%s" % (ALGO, iterations, sel.hex(), brut.hex())


def verifier(mot_de_passe, stocke):
    try:
        algo, iterations, sel, attendu = stocke.split("$")
        if algo != ALGO:
            return False
        brut = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), bytes.fromhex(sel), int(iterations))
        return hmac.compare_digest(brut.hex(), attendu)
    except (ValueError, AttributeError):
        return False


_LEURRE = None


def _leurre():
    """Empreinte factice : un identifiant inconnu coûte le même calcul qu'un vrai, sans révéler s'il existe."""
    global _LEURRE
    if _LEURRE is None:
        _LEURRE = hacher(secrets.token_hex(8))
    return _LEURRE


# ---------- Installation ----------
def lire_etat(cle):
    c = _base()
    try:
        r = c.execute("SELECT valeur FROM installation WHERE cle = ?", (cle,)).fetchone()
        return r["valeur"] if r else None
    finally:
        c.close()


def ecrire_etat(cle, valeur):
    c = _base()
    try:
        c.execute("INSERT OR REPLACE INTO installation VALUES (?, ?, ?)", (cle, str(valeur), time.time()))
    finally:
        c.close()


def nombre_comptes():
    c = _base()
    try:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        c.close()


# ---------- Comptes ----------
def _permissions(c, user_id):
    return {r[0] for r in c.execute("SELECT permission FROM user_permissions WHERE user_id = ?", (user_id,)).fetchall()}


def publique(u, accordees=None):
    """Ce qu'un compte montre de lui même à l'interface : jamais l'empreinte du mot de passe ni le chemin d'un fichier.
    avatar_v : date de la photo (sert à l'adresse de l'image), None sans photo. permissions : ce que le compte peut faire
    (toutes pour un administrateur) ; permissions_compte : ce qui est réglé pour lui (utile s'il redevient Utilisateur).
    bienvenue : la courte bienvenue de première connexion reste à montrer."""
    admin = u["role"] == "admin"
    accordees = set(accordees or ())
    return {"id": u["id"], "identifiant": u["identifiant"], "nom": u["nom"], "role": u["role"],
            "admin": admin, "cree_a": u["cree_a"], "derniere_connexion": u["derniere_connexion"],
            "actif": u["desactive_a"] is None, "avatar_v": int(u["avatar_maj"]) if u["avatar"] else None,
            "permissions": {p: admin or p in accordees for p in NOMS_PERMISSIONS},
            "permissions_compte": {p: p in accordees for p in NOMS_PERMISSIONS},
            "bienvenue": u["bienvenue_a"] is None}


def _publique(c, u):
    return publique(u, _permissions(c, u["id"]))


def a_permission(compte, permission):
    return bool(compte) and (compte.get("admin") or bool((compte.get("permissions") or {}).get(permission)))


def _nom(nom):
    nom = " ".join(str(nom or "").split())
    if not 1 <= len(nom) <= 60:
        raise Refus("Indiquez un prénom (60 caractères au plus).", "nom")
    return nom


def _mdp(mot_de_passe, champ="mot_de_passe"):
    mot_de_passe = str(mot_de_passe or "")
    if len(mot_de_passe) < MDP_MIN:
        raise Refus("Le mot de passe doit contenir au moins %d caractères." % MDP_MIN, champ)
    if len(mot_de_passe) > MDP_MAX:
        raise Refus("Mot de passe trop long.", champ)
    return mot_de_passe


def normaliser(nom, identifiant, mot_de_passe):
    nom = _nom(nom)
    identifiant = str(identifiant or "").strip().lower()
    if not IDENTIFIANT.match(identifiant):
        raise Refus("L'identifiant fait 3 à 32 caractères : lettres, chiffres, point, tiret ou soulignement.", "identifiant")
    return nom, identifiant, _mdp(mot_de_passe)


def creer_compte(nom, identifiant, mot_de_passe, role="utilisateur", premier=False):
    """Crée un compte. premier=True : seulement si aucun compte n'existe encore (création atomique, jamais deux premiers
    administrateurs même avec deux téléphones en même temps)."""
    nom, identifiant, mot_de_passe = normaliser(nom, identifiant, mot_de_passe)
    if role not in ROLES:
        raise Refus("Rôle inconnu.")
    empreinte_mdp = hacher(mot_de_passe)
    maintenant = time.time()
    c = _base()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            if premier and c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise Refus("Un compte administrateur existe déjà. Connectez vous.")
            if c.execute("SELECT 1 FROM users WHERE identifiant = ?", (identifiant,)).fetchone():
                raise Refus("Cet identifiant est déjà pris.", "identifiant")
            # Le premier administrateur vient de voir la présentation : pas de bienvenue en plus.
            cur = c.execute("INSERT INTO users (identifiant, nom, mot_de_passe, role, cree_a, maj_a, bienvenue_a) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (identifiant, nom, empreinte_mdp, role, maintenant, maintenant, maintenant if premier else None))
            for p in PERMISSIONS_DEFAUT:
                c.execute("INSERT INTO user_permissions VALUES (?, ?, ?)", (cur.lastrowid, p, maintenant))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return _publique(c, c.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        c.close()


def authentifier(identifiant, mot_de_passe):
    """Le compte si l'identifiant et le mot de passe sont bons et le compte actif, sinon None."""
    c = _base()
    try:
        u = c.execute("SELECT * FROM users WHERE identifiant = ?", (str(identifiant or "").strip().lower(),)).fetchone()
        ok = verifier(str(mot_de_passe or "")[:MDP_MAX], u["mot_de_passe"] if u else _leurre())
        if not (u and ok and u["desactive_a"] is None):
            return None
        c.execute("UPDATE users SET derniere_connexion = ? WHERE id = ?", (time.time(), u["id"]))
        return _publique(c, u)
    finally:
        c.close()


def lister_comptes():
    c = _base()
    try:
        lignes = c.execute("SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id AND s.revoquee_a IS NULL "
                           "AND s.expire_a > ?) AS sessions FROM users u ORDER BY u.id", (time.time(),)).fetchall()
        return [dict(_publique(c, u), sessions=u["sessions"]) for u in lignes]
    finally:
        c.close()


# ---------- Sessions ----------
def _empreinte(jeton):
    return hashlib.sha256(jeton.encode()).hexdigest()


def ouvrir_session(user_id, jours, appareil=""):
    """Nouvelle session pour ce navigateur : les autres sessions du compte (autres téléphones) restent valables."""
    jeton = secrets.token_urlsafe(32)
    maintenant, duree = time.time(), int(jours) * 86400
    c = _base()
    try:
        c.execute("INSERT INTO sessions (empreinte, user_id, cree_a, expire_a, duree_s, vue_a, appareil) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (_empreinte(jeton), user_id, maintenant, maintenant + duree, duree, maintenant, str(appareil)[:200]))
        c.execute("DELETE FROM sessions WHERE expire_a < ? OR revoquee_a < ?", (maintenant - 86400, maintenant - 86400 * 30))
    finally:
        c.close()
    return jeton, duree


def session(jeton):
    """(compte, durée) pour un jeton valable, sinon (None, 0). Une session utilisée après la moitié de sa durée est
    prolongée d'une durée complète, renvoyée pour reposer le cookie (0 sinon) : un téléphone utilisé régulièrement ne se
    déconnecte pas."""
    if not jeton or len(jeton) > 200:
        return None, 0
    maintenant = time.time()
    c = _base()
    try:
        r = c.execute("SELECT s.id AS sid, s.expire_a, s.duree_s, s.vue_a, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                      "WHERE s.empreinte = ? AND s.revoquee_a IS NULL", (_empreinte(jeton),)).fetchone()
        if not r or r["expire_a"] <= maintenant or r["desactive_a"] is not None:
            return None, 0
        renouvelee = r["expire_a"] - maintenant < r["duree_s"] / 2
        if renouvelee:
            c.execute("UPDATE sessions SET expire_a = ?, vue_a = ? WHERE id = ?", (maintenant + r["duree_s"], maintenant, r["sid"]))
        elif maintenant - r["vue_a"] > 300:
            c.execute("UPDATE sessions SET vue_a = ? WHERE id = ?", (maintenant, r["sid"]))
        return _publique(c, r), r["duree_s"] if renouvelee else 0
    finally:
        c.close()


def fermer_session(jeton):
    if not jeton:
        return
    c = _base()
    try:
        c.execute("UPDATE sessions SET revoquee_a = ? WHERE empreinte = ? AND revoquee_a IS NULL", (time.time(), _empreinte(jeton)))
    finally:
        c.close()


def revoquer_sessions(c, user_id, sauf_jeton=None):
    """Révoque toutes les sessions d'un compte (tous ses appareils), sauf éventuellement celle de ce navigateur."""
    if sauf_jeton:
        c.execute("UPDATE sessions SET revoquee_a = ? WHERE user_id = ? AND revoquee_a IS NULL AND empreinte != ?",
                  (time.time(), user_id, _empreinte(sauf_jeton)))
    else:
        c.execute("UPDATE sessions SET revoquee_a = ? WHERE user_id = ? AND revoquee_a IS NULL", (time.time(), user_id))


# ---------- Profil et administration (2.6.103) ----------
def compte(user_id):
    c = _base()
    try:
        u = c.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return _publique(c, u) if u else None
    finally:
        c.close()


def lire_preference(user_id, cle, defaut=None):
    c = _base()
    try:
        r = c.execute("SELECT valeur FROM user_preferences WHERE user_id = ? AND cle = ?", (int(user_id), str(cle))).fetchone()
        return r["valeur"] if r else defaut
    finally:
        c.close()


def ecrire_preference(user_id, cle, valeur):
    cle, valeur = str(cle), str(valeur)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", cle) or len(valeur) > 200:
        raise Refus("Préférence invalide.")
    def f(c):
        _cible(c, user_id)
        c.execute("INSERT INTO user_preferences(user_id,cle,valeur,maj_a) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id,cle) DO UPDATE SET valeur=excluded.valeur, maj_a=excluded.maj_a",
                  (int(user_id), cle, valeur, time.time()))
    _modifier(f)


def _modifier(fonction):
    """Exécute fonction(c) dans une transaction exclusive : les règles (dernier administrateur) se vérifient et
    s'appliquent d'un seul tenant, même avec deux administrateurs qui agissent en même temps."""
    c = _base()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            resultat = fonction(c)
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return resultat
    finally:
        c.close()


def _cible(c, user_id):
    u = c.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not u:
        raise Refus("Compte introuvable.")
    return u


def _admins_actifs_hors(c, user_id):
    return c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND desactive_a IS NULL AND id != ?", (user_id,)).fetchone()[0]


def modifier_nom(user_id, nom):
    nom = _nom(nom)
    def f(c):
        _cible(c, user_id)
        c.execute("UPDATE users SET nom = ?, maj_a = ? WHERE id = ?", (nom, time.time(), user_id))
        return _publique(c, _cible(c, user_id))
    return _modifier(f)


def changer_mot_de_passe(user_id, actuel, nouveau, jeton_courant=None):
    """Par le compte lui même : l'ancien mot de passe est exigé ; ses autres appareils sont déconnectés."""
    nouveau = _mdp(nouveau, "nouveau")
    def f(c):
        u = _cible(c, user_id)
        if not verifier(str(actuel or "")[:MDP_MAX], u["mot_de_passe"]):
            raise Refus("Mot de passe actuel incorrect.", "actuel")
        c.execute("UPDATE users SET mot_de_passe = ?, maj_a = ? WHERE id = ?", (hacher(nouveau), time.time(), user_id))
        revoquer_sessions(c, user_id, sauf_jeton=jeton_courant)
    _modifier(f)


def reinitialiser_mot_de_passe(user_id, nouveau):
    """Par un administrateur : nouveau mot de passe, toutes les sessions du compte révoquées."""
    nouveau = _mdp(nouveau)
    def f(c):
        _cible(c, user_id)
        c.execute("UPDATE users SET mot_de_passe = ?, maj_a = ? WHERE id = ?", (hacher(nouveau), time.time(), user_id))
        revoquer_sessions(c, user_id)
        return _publique(c, _cible(c, user_id))
    return _modifier(f)


def changer_role(user_id, role):
    if role not in ROLES:
        raise Refus("Rôle inconnu.", "role")
    def f(c):
        u = _cible(c, user_id)
        if u["role"] == "admin" and role != "admin" and u["desactive_a"] is None and not _admins_actifs_hors(c, u["id"]):
            raise Refus("C'est le dernier administrateur actif : KamCiné doit en garder au moins un.", "role")
        c.execute("UPDATE users SET role = ?, maj_a = ? WHERE id = ?", (role, time.time(), u["id"]))
        return _publique(c, _cible(c, u["id"]))
    return _modifier(f)


def changer_activation(user_id, actif):
    """Désactiver : plus de connexion possible et toutes ses sessions révoquées (une réactivation ne les ranime pas)."""
    def f(c):
        u = _cible(c, user_id)
        if actif:
            c.execute("UPDATE users SET desactive_a = NULL, maj_a = ? WHERE id = ?", (time.time(), u["id"]))
        else:
            if u["role"] == "admin" and u["desactive_a"] is None and not _admins_actifs_hors(c, u["id"]):
                raise Refus("C'est le dernier administrateur actif : KamCiné doit en garder au moins un.")
            if u["desactive_a"] is None:
                c.execute("UPDATE users SET desactive_a = ?, maj_a = ? WHERE id = ?", (time.time(), time.time(), u["id"]))
            revoquer_sessions(c, u["id"])
        return _publique(c, _cible(c, u["id"]))
    return _modifier(f)


# ---------- Photos des comptes ----------
AVATAR_MIN, AVATAR_MAX, AVATAR_COTE_MAX = 500, 2_000_000, 4096
_FICHIER_AVATAR = re.compile(r"^\d+\.jpg$")


def dossier_avatars():
    return chemins.donnee("avatars")


def jpeg_valide(data):
    """Une vraie image JPEG de taille raisonnable : début et fin JPEG, puis dimensions lues dans l'en tête (1 à 4096 px).
    Le navigateur recadre et réencode la photo en 512 px ; tout autre contenu est refusé."""
    if not (AVATAR_MIN <= len(data) <= AVATAR_MAX) or data[:3] != b"\xff\xd8\xff" or b"\xff\xd9" not in data[-64:]:
        return False
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return False
        marqueur = data[i + 1]
        if marqueur == 0xFF:
            i += 1
            continue
        longueur = int.from_bytes(data[i + 2:i + 4], "big")
        if marqueur in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            hauteur, largeur = int.from_bytes(data[i + 5:i + 7], "big"), int.from_bytes(data[i + 7:i + 9], "big")
            return 0 < hauteur <= AVATAR_COTE_MAX and 0 < largeur <= AVATAR_COTE_MAX
        if marqueur == 0xDA or longueur < 2:
            return False
        i += 2 + longueur
    return False


def chemin_avatar(user_id):
    """Le fichier de la photo d'un compte, ou None. Le nom vient de la base et n'est suivi que s'il a la forme <id>.jpg."""
    c = _base()
    try:
        u = c.execute("SELECT id, avatar FROM users WHERE id = ?", (int(user_id),)).fetchone()
    finally:
        c.close()
    if not u or not u["avatar"] or u["avatar"] != "%d.jpg" % u["id"] or not _FICHIER_AVATAR.match(u["avatar"]):
        return None
    chemin = os.path.join(dossier_avatars(), u["avatar"])
    return chemin if os.path.isfile(chemin) else None


def _ecrire_avatar(user_id, data):
    dossier = dossier_avatars()
    os.makedirs(dossier, exist_ok=True)
    final = os.path.join(dossier, "%d.jpg" % int(user_id))
    temporaire = final + ".%s.tmp" % secrets.token_hex(4)
    fd = os.open(temporaire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(temporaire, final)   # remplacement d'un seul coup : jamais une photo à moitié écrite
    except Exception:
        if os.path.exists(temporaire):
            os.remove(temporaire)
        raise


def definir_avatar(user_id, data):
    if not jpeg_valide(data):
        raise Refus("Image refusée : une photo JPEG de 2 Mo au plus.")
    def f(c):
        _cible(c, user_id)
        _ecrire_avatar(user_id, data)
        c.execute("UPDATE users SET avatar = ?, avatar_maj = ?, maj_a = ? WHERE id = ?",
                  ("%d.jpg" % int(user_id), time.time(), time.time(), int(user_id)))
        return _publique(c, _cible(c, user_id))
    return _modifier(f)


def retirer_avatar(user_id):
    def f(c):
        _cible(c, user_id)
        c.execute("UPDATE users SET avatar = NULL, avatar_maj = NULL, maj_a = ? WHERE id = ?", (time.time(), int(user_id)))
        return _publique(c, _cible(c, user_id))
    compte_ = _modifier(f)
    chemin = os.path.join(dossier_avatars(), "%d.jpg" % int(user_id))
    if os.path.exists(chemin):
        os.remove(chemin)
    return compte_


def rattacher_photo_ancienne(chemin):
    """Photo de profil d'avant les comptes (app/profil.jpg, une seule pour toute l'installation) : copiée comme photo du
    compte seulement sans ambiguïté, c'est à dire quand l'installation n'a qu'un compte et qu'il n'a pas encore de photo.
    Une seule fois (installation.photo_ancienne). L'ancien fichier reste en place pour un retour arrière. Renvoie l'id du
    compte qui l'a reçue, sinon None."""
    if lire_etat("photo_ancienne") or not chemin or not os.path.isfile(chemin):
        return None
    c = _base()
    try:
        lignes = c.execute("SELECT id, avatar FROM users").fetchall()
    finally:
        c.close()
    if not lignes:
        return None                                  # pas encore de compte : on réessaiera à la création du premier
    if len(lignes) > 1:
        ecrire_etat("photo_ancienne", "ignoree : plusieurs comptes")
        return None
    u = lignes[0]
    with open(chemin, "rb") as f:
        data = f.read()
    if u["avatar"] or not jpeg_valide(data):
        ecrire_etat("photo_ancienne", "ignoree : " + ("photo déjà choisie" if u["avatar"] else "image illisible"))
        return None
    definir_avatar(u["id"], data)
    ecrire_etat("photo_ancienne", "compte %d" % u["id"])
    return u["id"]


# ---------- Permissions et bienvenue (2.6.104) ----------
def changer_permission(user_id, permission, accordee):
    if permission not in NOMS_PERMISSIONS:
        raise Refus("Permission inconnue.")
    def f(c):
        _cible(c, user_id)
        if accordee:
            c.execute("INSERT OR IGNORE INTO user_permissions VALUES (?, ?, ?)", (int(user_id), permission, time.time()))
        else:
            c.execute("DELETE FROM user_permissions WHERE user_id = ? AND permission = ?", (int(user_id), permission))
        return _publique(c, _cible(c, user_id))
    return _modifier(f)


def bienvenue_vue(user_id):
    def f(c):
        c.execute("UPDATE users SET bienvenue_a = COALESCE(bienvenue_a, ?) WHERE id = ?", (time.time(), int(user_id)))
        return _publique(c, _cible(c, user_id))
    return _modifier(f)


# ---------- Favoris de chaque compte (2.6.104) ----------
def _favori(r):
    return {"type": r["media_type"], "id": r["tmdb_id"], "titre": r["titre"], "affiche": r["affiche"], "annee": r["annee"], "t": r["cree_a"]}


def favoris(user_id, media_type=None):
    c = _base()
    try:
        if media_type in ("movie", "tv"):
            lignes = c.execute("SELECT * FROM user_favorites WHERE user_id = ? AND media_type = ? ORDER BY cree_a DESC",
                               (int(user_id), media_type)).fetchall()
        else:
            lignes = c.execute("SELECT * FROM user_favorites WHERE user_id = ? ORDER BY cree_a DESC", (int(user_id),)).fetchall()
        return [_favori(r) for r in lignes]
    finally:
        c.close()


def est_favori(user_id, media_type, tmdb_id):
    c = _base()
    try:
        return c.execute("SELECT 1 FROM user_favorites WHERE user_id = ? AND media_type = ? AND tmdb_id = ?",
                         (int(user_id), media_type, int(tmdb_id))).fetchone() is not None
    finally:
        c.close()


def basculer_favori(user_id, media_type, tmdb_id, titre="", affiche=None, annee=""):
    """Ajoute ou retire un favori de CE compte ; renvoie True s'il est maintenant favori."""
    def f(c):
        cle = (int(user_id), media_type, int(tmdb_id))
        if c.execute("SELECT 1 FROM user_favorites WHERE user_id = ? AND media_type = ? AND tmdb_id = ?", cle).fetchone():
            c.execute("DELETE FROM user_favorites WHERE user_id = ? AND media_type = ? AND tmdb_id = ?", cle)
            return False
        c.execute("INSERT INTO user_favorites VALUES (?, ?, ?, ?, ?, ?, ?)",
                  cle + (str(titre or "")[:200], affiche, str(annee or "")[:4], time.time()))
        return True
    return _modifier(f)


def rattacher_favoris_anciens(anciens):
    """Favoris d'avant les comptes (favoris.json, communs) : ils étaient ceux du premier administrateur, qui les reçoit
    une seule fois (installation.favoris_anciens). favoris.json n'est ni modifié ni supprimé, pour un retour arrière.
    Sans compte encore, rien n'est fait : on réessaiera à la création du premier administrateur."""
    if lire_etat("favoris_anciens"):
        return None
    c = _base()
    try:
        r = c.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    finally:
        c.close()
    if not r:
        return None
    uid = r["id"]
    def f(c):
        for x in anciens or []:
            try:
                if x["type"] not in ("movie", "tv") or int(x["id"]) <= 0:
                    continue
                c.execute("INSERT OR IGNORE INTO user_favorites VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (uid, x["type"], int(x["id"]), str(x.get("titre") or "")[:200], x.get("affiche"),
                           str(x.get("annee") or "")[:4], float(x.get("t") or time.time())))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        c.execute("INSERT OR REPLACE INTO installation VALUES (?, ?, ?)", ("favoris_anciens", "compte %d" % uid, time.time()))
    _modifier(f)
    return uid


# ---------- Activité de chaque compte (2.6.104) ----------
GENRES_ACTIVITE = ("demande", "seance")


def noter_activite(user_id, genre, media_type=None, tmdb_id=None, details=""):
    """Qui a fait quoi : une demande envoyée, une séance lancée. Ne remplace aucun journal existant."""
    if not user_id or genre not in GENRES_ACTIVITE:
        return
    c = _base()
    try:
        c.execute("INSERT INTO user_activity (user_id, genre, media_type, tmdb_id, details, cree_a) VALUES (?, ?, ?, ?, ?, ?)",
                  (int(user_id), genre, media_type if media_type in ("movie", "tv") else None,
                   int(tmdb_id) if tmdb_id else None, str(details or "")[:200], time.time()))
    finally:
        c.close()


def medias_actifs(user_id, genre, page=1, taille=18):
    """Médias distincts d'une activité de ce compte, du plus récent au plus ancien : [(type, id)], et s'il en reste."""
    c = _base()
    try:
        lignes = c.execute("SELECT media_type, tmdb_id, MAX(cree_a) AS t FROM user_activity WHERE user_id = ? AND genre = ? "
                           "AND tmdb_id IS NOT NULL AND media_type IS NOT NULL GROUP BY media_type, tmdb_id ORDER BY t DESC LIMIT ? OFFSET ?",
                           (int(user_id), genre, taille + 1, (max(1, page) - 1) * taille)).fetchall()
    finally:
        c.close()
    return [(r["media_type"], r["tmdb_id"]) for r in lignes[:taille]], len(lignes) > taille


def compter_activite(user_id, genre, distincts=False):
    c = _base()
    try:
        if distincts:
            return c.execute("SELECT COUNT(*) FROM (SELECT DISTINCT media_type, tmdb_id FROM user_activity WHERE user_id = ? "
                             "AND genre = ? AND tmdb_id IS NOT NULL)", (int(user_id), genre)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM user_activity WHERE user_id = ? AND genre = ?", (int(user_id), genre)).fetchone()[0]
    finally:
        c.close()


# ---------- Inscriptions publiques (2.6.105) ----------
def inscriptions_ouvertes():
    """Un administrateur peut autoriser les personnes qui ont accès à cette installation à créer leur compte (Utilisateur).
    Fermées par défaut."""
    return lire_etat("inscriptions_ouvertes") == "1"


def regler_inscriptions(ouvertes):
    ecrire_etat("inscriptions_ouvertes", "1" if ouvertes else "0")
    return inscriptions_ouvertes()
