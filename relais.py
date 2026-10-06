"""Séance reprise en cours de film : le service prend le relais (entracte, générique) sans seance.py."""
import asyncio, os, random, threading, time
import lumieres
import lecture_infuse
from etat_seance import FinFilm, FIN_APRES_GENERIQUE_S, autre_lecture_active

ENTRACTE_VIDEO = "ga_66cp4Jrc"
INFUSE = "com.firecore.infuse"
# Point 90 : même seuil que seance.py (SAUT_MANUEL_S), dupliqué comme les autres constantes de ce fichier (piège 4
# de CLAUDE.md) : relais.py ne peut pas importer seance.py, qui exige COMP et AIR dès l'import.
SAUT_MANUEL_S = 5
# Lot 2.6.89 : un recul d'au moins ce nombre de secondes sous le seuil du générique ramène la séance au film ou à l'épisode.
RETOUR_AVANT_GENERIQUE_S = 30
# Lot 2.6.89 : après le générique d'un épisode, délai laissé à Infuse pour enchaîner l'épisode suivant (compte à rebours,
# chargement, identification) avant de conclure à la fin de la séance.
ATTENTE_EPISODE_SUIVANT_S = 60


async def _sequence_async(etapes, appletv_id=None):
    import pyatv
    from pyatv.const import Protocol
    import integrations
    appareil = integrations.appletv(appletv_id)  # cible par séance, sinon choix global historique
    if not appareil:
        raise RuntimeError("Apple TV non configurée")
    loop = asyncio.get_running_loop()
    confs = await pyatv.scan(loop, identifier=appareil["identifiant"], timeout=5)
    if not confs:
        raise RuntimeError("Apple TV introuvable")
    conf = confs[0]
    conf.set_credentials(Protocol.Companion, appareil["companion"])
    if appareil["airplay"]:
        conf.set_credentials(Protocol.AirPlay, appareil["airplay"])
    atv = await pyatv.connect(conf, loop)
    try:
        for e in etapes:
            if e[0] == "launch":
                await atv.apps.launch_app(e[1])
            elif e[0] == "attendre":
                await asyncio.sleep(e[1])
            elif e[0] == "skip_backward":
                await atv.remote_control.skip_backward(e[1])
            elif e[0] == "play":
                await atv.remote_control.play()
            elif e[0] == "menu":
                await atv.remote_control.menu()
    finally:
        taches = atv.close()
        if taches:
            await asyncio.gather(*taches)


class Relais:
    def __init__(self, hote):
        self.h = hote
        self.actif = False
        self.jeton = 0
        self.cmd = None
        self.reprise_anticipee = False
        self._appletv_id = None

    def demarrer(self, ctx):
        if self.actif:
            return False
        ctx = dict(ctx or {})
        if not ctx.get("appletv_id"):
            try:
                ctx["appletv_id"] = self.h.appletv_id()
            except Exception:
                pass
        self._appletv_id = ctx.get("appletv_id") or None
        self.jeton += 1
        self.actif, self.cmd = True, None
        threading.Thread(target=self._boucle, args=(ctx, self.jeton), daemon=True).start()
        return True

    def arreter(self):
        self.jeton += 1
        self.actif = False
        lumieres.SEANCE["actif"] = False

    def entracte(self, duree, annuler_classique):
        self.cmd = ("entracte", int(duree), bool(annuler_classique))

    def reprendre_entracte(self):
        """Écourte l'entracte en cours : le film reprend tout de suite au lieu d'attendre la fin de la durée prévue."""
        self.reprise_anticipee = True

    def _vivant(self, jeton):
        return self.actif and jeton == self.jeton

    def _sequence(self, etapes, appletv_id=None):
        appletv_id = appletv_id or self._appletv_id
        try:
            asyncio.run(_sequence_async(etapes, appletv_id))
            return
        except Exception as e:
            self.h.log("  (commandes groupées indisponibles : %s)" % str(e)[:60])
        for e in etapes:
            if e[0] == "launch":
                self.h.atv("launch_app=" + e[1], identifiant=appletv_id)
            elif e[0] == "attendre":
                time.sleep(e[1])
            elif e[0] == "skip_backward":
                # Même durée que la voie directe (réglage recul), syntaxe atvremote commande=argument (2.6.98) : sans elle,
                # Infuse reculait de son propre intervalle.
                self.h.atv("skip_backward=%d" % int(e[1]), identifiant=appletv_id)

    def _attendre(self, secondes, jeton):
        fin = time.time() + secondes
        while time.time() < fin and self._vivant(jeton):
            time.sleep(min(1, max(0, fin - time.time())))

    def _attendre_entracte(self, secondes, jeton):
        """Comme _attendre, mais s'arrête aussi si l'utilisateur demande de reprendre le film plus tôt."""
        fin = time.time() + secondes
        while time.time() < fin and self._vivant(jeton) and not self.reprise_anticipee:
            time.sleep(min(1, max(0, fin - time.time())))

    def _entracte(self, duree, cfg, jeton):
        h = self.h
        self.reprise_anticipee = False
        h.log("ENTRACTE : écran entracte pour %d secondes" % duree)
        h.atv("launch_app=youtube://www.youtube.com/watch?v=" + ENTRACTE_VIDEO)
        h.lumiere("entracte", 25)
        self._attendre_entracte(duree, jeton)
        if self.reprise_anticipee and self._vivant(jeton):
            h.log("Entracte écourtée à ta demande")
        self.reprise_anticipee = False
        self._retour(cfg, jeton)

    def _retour(self, cfg, jeton):
        """Retour au film après l'entracte. La séance peut être arrêtée à tout moment : on ne renvoie rien après un arrêt."""
        h = self.h
        if not self._vivant(jeton):
            return
        h.log("Reprise du film : retour à Infuse et recul immédiat")
        etapes = [("launch", INFUSE), ("attendre", 0.2)]
        if cfg["recul"] > 0:
            # 2.6.99 : recul à 0 veut dire aucun recul, comme dans seance.py. skip_backward(0) laissait Infuse choisir son intervalle.
            etapes.append(("skip_backward", cfg["recul"]))
        self._sequence(etapes)
        self._attendre(2, jeton)
        if not self._vivant(jeton):
            return
        p = h.playing_frais() if hasattr(h, "playing_frais") else h.playing()
        if p and p["etat"] == "Paused" and self._vivant(jeton):
            h.atv("play")
        if self._vivant(jeton):
            h.lumiere("noir", 4)

    def _duree_classique(self, cfg, test):
        if test:
            return 15
        lo, hi = sorted((cfg["entracte_min"], cfg["entracte_max"]))
        return random.randint(lo * 60, hi * 60)

    def _episode(self, ctx, cfg, test, meta, p, serie, premier, jeton):
        """Un film ou un épisode, de la première observation à sa fin. Renvoie (meta, observation) quand Infuse enchaîne l'épisode
        suivant de la même série (lot 2.6.89), None quand la séance s'arrête ou se termine."""
        h = self.h
        total = p["total"]
        prevu, skipped = None, False
        if premier:
            prevu = None if ctx.get("entracte_deja") else ctx.get("entracte_prevu")
            skipped = bool(ctx.get("intermission_skipped"))
            if (prevu is None and not serie and ctx.get("entracte_actif", cfg["entracte_actif"]) and not ctx.get("entracte_deja")
                    and not ctx.get("annuler_classique")):
                lo, hi = total * cfg["entracte_debut"] / 100.0, total * cfg["entracte_fin"] / 100.0
                if test:
                    prevu = p["pos"] + 20
                elif p["pos"] < hi - 60:
                    prevu = int(random.uniform(max(lo, p["pos"] + 60), hi))
                if prevu:
                    h.log("Entracte prévue à %ds (%d %%)" % (prevu, round(prevu / total * 100)))
        if serie:
            prevu = None
        cible, source = h.cible_generique(meta, total, cfg, test, p["pos"], serie)
        h.log(("Minutage du générique : vers %ds sur %ds (%s)" if source == "TheIntroDB"
               else "Générique attendu vers %ds sur %ds (%s)") % (cible, total, source))
        if premier and ctx.get("entracte_manuelle"):
            self._entracte(int(ctx["entracte_manuelle"]), cfg, jeton)
        suivi_fin = FinFilm(meta)
        dernier_p, dernier_t = p["pos"], time.time()
        credits_started = premier and bool(ctx.get("credits_done"))
        credits_t = time.time() if credits_started else None
        test_end = time.time() + 15 if test and credits_started else None
        vu_fait = credits_started
        autre_episode_depuis = silence_depuis = None
        while self._vivant(jeton):
            if self.cmd:
                c, self.cmd = self.cmd, None
                self._entracte(c[1], cfg, jeton)
                suivi_fin.patienter()
                if c[2]:
                    prevu = None
                continue
            p = h.playing()
            if serie and lecture_infuse.meme_serie_autre_episode(p, dict(meta or {}, total_fichier=(meta or {}).get("total_fichier") or total)):
                suivant = h.episode_suivant(meta, p) if p.get("total") else None
                if suivant:
                    return suivant, p
                # Même série, épisode pas encore publié ou pas encore résolu : ni fin ni interruption le temps qu'il le soit.
                autre_episode_depuis = autre_episode_depuis or time.time()
                if time.time() - autre_episode_depuis < ATTENTE_EPISODE_SUIVANT_S:
                    time.sleep(3)
                    continue
            else:
                autre_episode_depuis = None
            resultat = suivi_fin.observer(p)
            if resultat in ("terminee", "suspendue"):
                if serie and credits_started and not test and not autre_lecture_active(p):
                    # Après le générique d'un épisode, Infuse affiche le compte à rebours de l'épisode suivant : un court
                    # silence n'est pas encore la fin de la séance.
                    silence_depuis = silence_depuis or time.time()
                    if time.time() - silence_depuis < ATTENTE_EPISODE_SUIVANT_S:
                        time.sleep(3)
                        continue
                if resultat == "terminee" and not (serie and credits_started):
                    h.log("Fin naturelle du film")
                elif credits_started and not test:
                    # Film ou épisode quitté pendant le générique : la séance est finie, pas interrompue (lot 2.6.81).
                    h.log("Fin de séance au générique")
                    h.log("Séance terminée")
                else:
                    h.log("Le film s'est arrêté, séance conservée")
                return None
            if resultat in ("lecture", "pause"):
                silence_depuis = None
            if credits_started and not test and not serie and time.time() - credits_t >= FIN_APRES_GENERIQUE_S:
                h.log("Fin de séance au générique")
                h.log("Séance terminée")
                return None
            if resultat not in ("lecture", "pause"):
                time.sleep(3)
                continue
            maintenant = time.time()
            saut = (p["pos"] - dernier_p) - (maintenant - dernier_t)
            dernier_p, dernier_t = p["pos"], maintenant
            if credits_started and p["pos"] < cible - RETOUR_AVANT_GENERIQUE_S:
                # Lot 2.6.89 : un recul avant le seuil rend la main au film ou à l'épisode. La lumière suit une seule fois par
                # franchissement réel (marge de RETOUR_AVANT_GENERIQUE_S), jamais au gré des petites variations de position.
                credits_started, credits_t, test_end = False, None, None
                h.log("Retour avant le générique : %s continue" % ("l'épisode" if serie else "le film"))
                h.journal_generique("retour avant le seuil", p["pos"], seuil=cible, duree=total)
                h.lumiere("noir", 4)
            if prevu is not None and skipped and p["pos"] < prevu:
                skipped = False
                h.log("Entracte réarmée après retour avant le seuil")
            if prevu is not None and not skipped and p["pos"] >= prevu:
                if saut > SAUT_MANUEL_S:
                    skipped = True
                    h.log("Avance manuelle au delà de l'entracte prévue (%ds) : entracte annulée" % p["pos"])
                elif resultat == "lecture":
                    prevu = None
                    self._entracte(self._duree_classique(cfg, test), cfg, jeton)
                    suivi_fin.patienter()
                else:
                    time.sleep(3)
                    continue
                continue
            if resultat == "pause":
                time.sleep(3)
                continue
            if p["pos"] >= cible and not credits_started:
                credits_started, credits_t = True, time.time()
                h.log("Générique : TV gauche et droite")
                h.journal_generique("générique activé", p["pos"], seuil=cible, duree=total)
                h.lumiere("generique", 20)
                if test:
                    test_end = time.time() + 15
                elif not vu_fait:
                    vu_fait = True
                    h.marquer_vu(meta)
            if test_end is not None and time.time() >= test_end:
                h.log("Séance terminée")
                return None
            time.sleep(4)
        return None

    def _boucle(self, ctx, jeton):
        h = self.h
        lumieres.SEANCE["actif"] = True
        try:
            cfg, test, meta = h.charger(), ctx.get("test", True), ctx.get("meta")
            if ctx.get("reprendre_maintenant"):
                # entracte écourtée depuis une séance classique : l'écran montre l'entracte, pas le film, on revient d'abord
                self._retour(cfg, jeton)
                self._attendre(4, jeton)
                if not self._vivant(jeton):
                    return
            if not ctx.get("silencieux"):
                h.log("Séance reprise en cours d'épisode" if ctx.get("type") == "serie" else "Séance reprise en cours de film")
                h.lumiere("noir", 3)
            elif ctx.get("lumieres_reprise"):
                # Reprise demandée depuis la carte Séance interrompue : retour à l'ambiance du film, sans nouvelle ligne de scénario.
                h.lumiere("noir", 3)
            observation = FinFilm(meta)
            while self._vivant(jeton):
                p = h.playing()
                resultat = observation.observer(p)
                if resultat in ("terminee", "suspendue"):
                    h.log("Le film s'est arrêté, séance conservée")
                    return
                if resultat in ("lecture", "pause") and p.get("total"):
                    break
                self._attendre(3, jeton)
            if not self._vivant(jeton):
                return
            serie = ctx.get("type") == "serie"
            if not ctx.get("silencieux"):
                h.log(("Épisode : %s" if serie else "Film : %s") % p["titre"])
            premier = True
            while self._vivant(jeton):
                suite = self._episode(ctx, cfg, test, meta, p, serie, premier, jeton)
                if not suite:
                    return
                # Lot 2.6.89 : Infuse enchaîne l'épisode suivant, la même séance le suit avec un scénario neuf.
                meta, p, premier = suite[0], suite[1], False
        except Exception as e:
            h.log("Erreur du relais : %s" % str(e)[:100])
        finally:
            if jeton == self.jeton:
                self.actif = False
                lumieres.SEANCE["actif"] = False
