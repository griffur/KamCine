"""Métadonnées facultatives, capacités et images pyatv sans appareil."""
import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atvlive
from pyatv.interface import Playing
from pyatv.const import DeviceState, MediaType, FeatureState, FeatureName


class Observation(unittest.TestCase):
    def test_etat_precedent_survit_a_un_echec_bref_puis_expire_apres_echecs_repetes(self):
        live = atvlive.Live()
        live.etat = {"etat": "Playing", "app": "com.firecore.infuse", "titre": "Film"}
        live.t, live.pas, live.erreurs_consecutives = 100, 3, 1
        with patch.object(atvlive.time, "time", return_value=108), \
                patch.object(live, "observe", side_effect=lambda p, source: p):
            self.assertEqual(live.lire()["titre"], "Film")
        live.erreurs_consecutives = 3
        with patch.object(atvlive.time, "time", return_value=108):
            self.assertIsNone(live.lire())

    def test_deux_echecs_brefs_conservent_le_media_puis_la_grace_expire(self):
        live = atvlive.Live()
        live.etat = {"etat": "Playing", "app": "com.disney.disneyplus", "titre": "Z-O-M-B-I-E-S 2"}
        live.t, live.pas, live.erreurs_consecutives = 100, 12, 2
        with patch.object(atvlive.time, "time", return_value=119), patch.object(live, "observe", side_effect=lambda p, source: p):
            self.assertEqual(live.lire()["titre"], "Z-O-M-B-I-E-S 2")
        with patch.object(atvlive.time, "time", return_value=121):
            self.assertIsNone(live.lire(), "l'état expiré ne doit pas être conservé indéfiniment")

    def test_cibler_une_apple_tv_est_ephemere_et_reveille_le_polling(self):
        live = atvlive.Live()
        live.boucle, live.reveil = Mock(), Mock()
        live.cibler("chambre")
        self.assertEqual(live.target_id, "chambre")
        live.boucle.call_soon_threadsafe.assert_called_once_with(live.reveil.set)
        live.cibler(None)
        self.assertIsNone(live.target_id, "aucune préférence utilisateur ne modifie la configuration persistée")

    def test_type_inconnu_et_capacites_reelles(self):
        p = Playing(device_state=DeviceState.Playing, media_type=MediaType.Unknown,
                    title="Vidéo", content_identifier="dQw4w9WgXcQ")
        atv = SimpleNamespace(metadata=SimpleNamespace(playing=AsyncMock(return_value=p),
                         app=SimpleNamespace(identifier="com.google.ios.youtube", name="YouTube")),
                         power=Mock(), features=Mock())
        atv.features.get_feature.side_effect = lambda n: SimpleNamespace(state=(
            FeatureState.Available if n == FeatureName.PlayPause else FeatureState.Unavailable))
        r = asyncio.run(atvlive.Live()._lire(atv))
        self.assertEqual(r["media"], "Unknown")
        self.assertEqual(r["commandes"], {"play_pause": True, "back": False, "forward": False})
        self.assertEqual(atvlive.lien_youtube(r), "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertNotIn("app_active", r)

    def test_youtube_reconnu_si_application_absente_mais_url_native_fiable(self):
        p = Playing(device_state=DeviceState.Playing, media_type=MediaType.Unknown,
                    title="", content_identifier="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        atv = SimpleNamespace(metadata=SimpleNamespace(playing=AsyncMock(return_value=p), app=None),
                              power=Mock(), features=Mock())
        atv.features.get_feature.return_value = SimpleNamespace(state=FeatureState.Unavailable)
        resultat = asyncio.run(atvlive.Live()._lire(atv))
        self.assertEqual(resultat["app"], "com.google.ios.youtube")
        self.assertEqual(resultat["app_nom"], "YouTube")
        self.assertEqual(atvlive.lien_youtube(resultat), "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_url_non_youtube_ne_devient_pas_une_lecture_youtube(self):
        p = Playing(device_state=DeviceState.Playing, media_type=MediaType.Unknown,
                    content_identifier="https://youtube.com.example.invalid/watch?v=dQw4w9WgXcQ")
        atv = SimpleNamespace(metadata=SimpleNamespace(playing=AsyncMock(return_value=p), app=None),
                              power=Mock(), features=Mock())
        atv.features.get_feature.return_value = SimpleNamespace(state=FeatureState.Unavailable)
        resultat = asyncio.run(atvlive.Live()._lire(atv))
        self.assertIsNone(resultat["app"])

    def test_lien_ne_vient_jamais_du_titre_ou_du_hash(self):
        p = {"app": "com.google.ios.youtube", "titre": "dQw4w9WgXcQ", "hash": "dQw4w9WgXcQ"}
        for identifiant in (None, "", "https://youtube.com.evil/watch?v=dQw4w9WgXcQ", "file:///dQw4w9WgXcQ", "ancien"):
            self.assertIsNone(atvlive.lien_youtube(dict(p, content_identifier=identifiant)))
        for identifiant in ("dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
            self.assertEqual(atvlive.lien_youtube(dict(p, content_identifier=identifiant)), "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_image_tardive_et_effacement_a_arret(self):
        live = atvlive.Live()
        live.etat = {"app": "com.google.ios.youtube", "etat": "Playing", "hash": "video1"}
        art = SimpleNamespace(bytes=b"image", mimetype="image/png")
        atv = SimpleNamespace(metadata=SimpleNamespace(artwork=AsyncMock(side_effect=[None, art])))
        with patch.object(atvlive.time, "time", return_value=100):
            asyncio.run(live._image(atv))
            asyncio.run(live._image(atv))
        self.assertEqual(atv.metadata.artwork.call_count, 1)
        with patch.object(atvlive.time, "time", return_value=111):
            asyncio.run(live._image(atv))
        self.assertEqual(live.jaquette["octets"], b"image")
        self.assertNotEqual(atvlive.cle_image(live.etat), atvlive.cle_image(dict(live.etat, app="autre.app")))
        self.assertNotEqual(atvlive.cle_image(live.etat), atvlive.cle_image(dict(live.etat, content_identifier="nouvelle")))
        live.etat["etat"] = "Idle"
        asyncio.run(live._image(atv))
        self.assertIsNone(live.jaquette)

    def test_retour_au_premier_plan_demande_une_observation_rapide(self):
        live = atvlive.Live()
        live.etat, live.t, live.pas = {"etat": "Idle"}, 97, 20
        live.boucle, live.reveil = Mock(), Mock()
        with patch.object(atvlive.time, "time", return_value=100), patch.object(atvlive, "_pas_actif", return_value=2):
            live.lire()
        live.boucle.call_soon_threadsafe.assert_called_once_with(live.reveil.set)


class Commandes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.live = atvlive.Live()
        self.live._lecture_lock = asyncio.Lock()
        self.live.reveil = asyncio.Event()
        self.p = {"app": "com.firecore.infuse", "titre": "Film", "media": "Video",
                  "etat": "Playing", "pos": 100, "total": 7200, "position_connue": True,
                  "fonctions": {k: True for k in ("play", "pause", "play_pause", "back", "forward", "position")}}
        self.rc = SimpleNamespace(**{k: AsyncMock() for k in ("play", "pause", "play_pause", "skip_backward", "skip_forward", "set_position")})
        self.live._atv = SimpleNamespace(remote_control=self.rc)
        async def lire(atv):
            p = dict(self.p)
            p["commandes"] = {k: atvlive.plan_commande(p, k) is not None for k in ("play_pause", "back", "forward")}
            return p
        self.live._lire = lire

    async def test_play_pause_explicites_et_retour_etat_immediat(self):
        async def pause():
            self.p["etat"] = "Paused"
        async def play():
            self.p["etat"] = "Playing"
        self.rc.pause.side_effect, self.rc.play.side_effect = pause, play
        jeton = atvlive.jeton_lecture(self.p)
        r = await self.live._commander("play_pause", jeton)
        self.assertEqual(r["lecteur"]["etat"], "Paused")
        self.assertTrue(r["lecteur"]["commandes"]["play_pause"])
        r = await self.live._commander("play_pause", jeton)
        self.assertEqual(r["lecteur"]["etat"], "Playing")
        self.rc.pause.assert_awaited_once()
        self.rc.play.assert_awaited_once()
        self.rc.play_pause.assert_not_awaited()

    async def test_sauts_dix_secondes_en_lecture_et_pause(self):
        for etat in ("Playing", "Paused"):
            self.p["etat"] = etat
            self.live.etat = dict(self.p)
            self.live.t = time.time()
            for cmd in ("back", "forward"):
                self.assertTrue((await self.live._commander(cmd, ""))["ok"])
        self.assertEqual(self.rc.skip_backward.await_count, 2)
        self.rc.skip_backward.assert_awaited_with(10)
        self.rc.skip_forward.assert_awaited_with(10)
        self.rc.play.assert_not_awaited()

    async def test_repli_position_borne_sans_perdre_la_pause(self):
        self.p.update(etat="Paused", fonctions={"position": True})
        for pos, cmd, cible in [(5, "back", 0), (100, "forward", 110), (7198, "forward", 7200)]:
            self.p["pos"] = pos
            self.live.etat = dict(self.p)
            self.live.t = time.time()
            self.assertTrue((await self.live._commander(cmd, ""))["ok"])
            self.rc.set_position.assert_awaited_with(cible)
        self.rc.play.assert_not_awaited()
        self.p["position_connue"] = False
        self.live.etat = dict(self.p)
        self.live.t = time.time()
        self.assertFalse((await self.live._commander("forward", ""))["ok"])

    async def test_toggle_seul_et_capacites_indisponibles(self):
        self.p["fonctions"] = {"play_pause": True}
        self.live.etat = dict(self.p)
        self.live.t = time.time()
        self.assertTrue((await self.live._commander("play_pause", ""))["ok"])
        self.rc.play_pause.assert_awaited_once()
        self.assertFalse((await self.live._commander("back", ""))["ok"])
        self.rc.skip_backward.assert_not_awaited()

    async def test_changement_infuse_youtube_rejette_ancienne_commande(self):
        attendu = atvlive.jeton_lecture(self.p)
        self.p.update(app="com.google.ios.youtube", titre="Entracte")
        self.live.etat = dict(self.p)
        self.live.t = time.time()
        r = await self.live._commander("play_pause", attendu)
        self.assertFalse(r["ok"])
        self.assertEqual(r["lecteur"]["app"], "com.google.ios.youtube")
        self.rc.pause.assert_not_awaited()
        self.p.update(app="com.firecore.infuse", titre="Film", etat="Paused")
        self.live.etat = dict(self.p)
        self.live.t = time.time()
        self.assertTrue((await self.live._commander("play_pause", atvlive.jeton_lecture(self.p)))["ok"])
        self.rc.play.assert_awaited_once()

    async def test_aucune_commande_sans_media_reel(self):
        for variation in ({"etat": "Idle"}, {"etat": "Stopped"}, {"veille": True}, {"etat": "Unknown"}):
            ancien = dict(self.p)
            self.p.update(variation)
            self.live.etat = dict(self.p)
            self.live.t = time.time()
            self.assertFalse((await self.live._commander("play_pause", ""))["ok"])
            self.p = ancien
        self.rc.pause.assert_not_awaited()

    async def test_erreur_envoi_ne_rejoue_pas_une_autre_commande(self):
        self.rc.pause.side_effect = TimeoutError("réponse perdue")
        with self.assertRaises(TimeoutError):
            await self.live._commander("play_pause", "")
        self.rc.pause.assert_awaited_once()
        self.rc.play_pause.assert_not_awaited()
        self.assertTrue(self.live.reveil.is_set())

    async def test_commande_rapide_ne_fait_pas_de_relecture_reseau_apres_envoi(self):
        self.live._lire = AsyncMock(side_effect=[dict(self.p), RuntimeError("lecture indisponible")])
        r = await self.live._commander("play_pause", "")
        self.assertTrue(r["ok"])
        self.assertTrue(r["lecteur"]["actif"])
        self.assertEqual(self.live._lire.await_count, 1)
        self.rc.pause.assert_awaited_once()


class Rapide(unittest.IsolatedAsyncioTestCase):
    """Commandes d'administration (quitter un film, arrêter YouTube) : rapides, sans vérification de média."""
    async def asyncSetUp(self):
        self.live = atvlive.Live()
        self.live._lecture_lock = asyncio.Lock()
        self.live.reveil = asyncio.Event()
        self.rc = SimpleNamespace(menu=AsyncMock(), pause=AsyncMock())
        self.apps = SimpleNamespace(launch_app=AsyncMock())
        self.live._atv = SimpleNamespace(remote_control=self.rc, apps=self.apps)

    async def test_menu_pause_ouvrir_app_envoient_la_bonne_commande(self):
        self.assertTrue(await self.live._rapide_async(lambda atv: atv.remote_control.menu()))
        self.rc.menu.assert_awaited_once()
        self.assertTrue(await self.live._rapide_async(lambda atv: atv.remote_control.pause()))
        self.rc.pause.assert_awaited_once()
        self.assertTrue(await self.live._rapide_async(lambda atv: atv.apps.launch_app("com.firecore.infuse")))
        self.apps.launch_app.assert_awaited_once_with("com.firecore.infuse")
        self.assertTrue(self.live.reveil.is_set())

    async def test_sans_connexion_renvoie_faux(self):
        self.live._atv = None
        self.assertFalse(await self.live._rapide_async(lambda atv: atv.remote_control.menu()))

    def test_pas_de_boucle_renvoie_faux_sans_bloquer(self):
        live = atvlive.Live()
        self.assertFalse(live.menu())
        self.assertFalse(live.pause())
        self.assertFalse(live.ouvrir_app("com.firecore.infuse"))


if __name__ == "__main__":
    unittest.main()
