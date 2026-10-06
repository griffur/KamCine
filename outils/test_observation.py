"""Une répétition de transport ne doit jamais fabriquer une preuve de lecture."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observation import ObservationLedger
import etat_seance


class Freshness(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'observations.json')
        self.ledger = ObservationLedger(self.path, lambda: self.now)
        self.video = dict(app='com.google.ios.youtube', titre='Ancienne vidéo',
                          etat='Paused', pos=30, total=100, media='Video')

    def observe(self, **changes):
        return self.ledger.observe(dict(self.video, **changes), 'test', 120)

    def test_identical_callbacks_and_reconnections_do_not_refresh(self):
        self.observe()
        for source in ('live_poll', 'callback', 'cli', 'reconnect') * 100:
            self.now += 1
            p = self.ledger.observe(dict(self.video, lu_a=self.now), source, 120)
        self.assertEqual(p['changed_at'], 1000)
        self.assertTrue(p['stale'])
        self.assertFalse(etat_seance.media_reel(p))

    def test_interleaved_other_media_idle_and_duration_do_not_reset_pause(self):
        self.observe()
        self.now += 121
        self.observe(app='com.firecore.infuse', titre='Film', etat='Playing')
        self.observe(etat='Idle')
        self.assertTrue(self.observe(total=101, hash='different')['stale'])

    def test_stop_then_old_paused_or_playing_is_not_new_activity(self):
        self.observe()
        self.ledger.invalidate()
        self.now += 20
        self.assertTrue(self.observe()['stale'])
        self.assertTrue(self.observe(etat='Playing')['stale'])
        self.assertFalse(self.observe(etat='Playing', pos=33)['stale'])

    def test_restart_and_cli_keep_expiration_and_stop_barrier(self):
        self.observe()
        self.now += 121
        self.ledger = ObservationLedger(self.path, lambda: self.now)
        self.assertTrue(self.observe()['stale'])
        self.ledger.invalidate()
        self.ledger = ObservationLedger(self.path, lambda: self.now)
        self.assertTrue(self.observe()['stale'])

    def test_new_unseen_paused_after_stop_needs_progress(self):
        self.ledger.invalidate()
        self.assertTrue(self.observe()['stale'])
        self.assertFalse(self.observe(etat='Playing', pos=40)['stale'])

    def test_real_pause_seek_before_invalidation_is_activity(self):
        self.observe()
        self.now += 121
        self.assertFalse(self.observe(pos=40)['stale'])
        self.now += 121
        self.assertTrue(self.observe(pos=40)['stale'])

    def test_infuse_is_not_expired_as_external_pause(self):
        self.ledger.invalidate()
        p = self.observe(app='com.firecore.infuse')
        self.assertNotIn('stale', p)
        self.assertTrue(etat_seance.media_reel(p))

    def test_ledger_keeps_only_recent_contents(self):
        import observation
        for i in range(observation.MAX_RECORDS + 20):
            self.now += 1
            self.observe(titre='Vidéo %d' % i)
        self.assertEqual(len(self.ledger.records), observation.MAX_RECORDS)
        self.now += 1
        self.assertEqual(self.observe(titre='Vidéo %d' % (observation.MAX_RECORDS + 19))['changed_at'], self.now - 1)
        self.now += 1
        self.assertEqual(self.observe(titre='Vidéo 0')['changed_at'], self.now)


class Generation(unittest.TestCase):
    """Lot 2.6.87 : une pause antérieure à une lecture plus récente (Infuse compris) n'est plus une lecture actuelle."""
    def setUp(self):
        self.now = 1000
        self.ledger = ObservationLedger(None, lambda: self.now)
        self.yt = dict(app='com.google.ios.youtube', titre='Ancienne vidéo', media='Video', total=7340)

    def obs(self, p, source='live_poll'):
        return self.ledger.observe(p, source, 120)

    def youtube(self, etat, pos):
        return self.obs(dict(self.yt, etat=etat, pos=pos))

    def infuse(self, etat, pos, titre='Reacher - S1 \u2219 E5 - Aucune excuse'):
        return self.obs(dict(app='com.firecore.infuse', titre=titre, etat=etat, pos=pos, total=2869, media='Video'))

    def test_a_ancienne_pause_youtube_ne_revient_pas_apres_infuse(self):
        self.youtube('Playing', 690); self.now += 3; self.youtube('Playing', 693); self.now += 3
        self.assertFalse(self.youtube('Paused', 699)['stale'], 'Vraie pause YouTube récente')
        self.now += 20; self.infuse('Playing', 100); self.now += 3; self.infuse('Playing', 103)
        self.now += 30
        p = self.youtube('Paused', 699)
        self.assertTrue(p['stale'], "Même 50 s après seulement, la pause d'avant Reacher est dépassée")
        self.assertEqual(p['stale_reason'], 'superseded')
        self.assertFalse(etat_seance.media_reel(p))

    def test_b_nouvelle_video_youtube_apres_infuse_apparait_tout_de_suite(self):
        self.youtube('Playing', 10); self.now += 3; self.youtube('Playing', 13); self.youtube('Paused', 13)
        self.now += 5; self.infuse('Playing', 100); self.now += 3; self.infuse('Playing', 103)
        self.now += 5
        nouvelle = self.obs(dict(self.yt, titre='Nouvelle vidéo', etat='Playing', pos=0))
        self.assertFalse(nouvelle['stale'])
        self.assertTrue(etat_seance.media_reel(nouvelle))
        self.now += 3
        self.assertTrue(etat_seance.autre_lecture_active(self.obs(dict(self.yt, titre='Nouvelle vidéo', etat='Playing', pos=3))))

    def test_b_bis_ancienne_video_reprise_redevient_actuelle(self):
        self.youtube('Playing', 10); self.now += 3; self.youtube('Playing', 13); self.youtube('Paused', 13)
        self.now += 5; self.infuse('Playing', 100); self.now += 3; self.infuse('Playing', 103)
        self.now += 5; self.youtube('Playing', 13); self.now += 3
        self.assertFalse(self.youtube('Playing', 16)['stale'], 'Relancée pour de vrai : elle progresse, elle est actuelle')

    def test_c_pause_infuse_n_est_jamais_perimee(self):
        self.infuse('Playing', 100); self.now += 3; self.infuse('Playing', 103); self.now += 300
        p = self.infuse('Paused', 103)
        self.assertNotIn('stale', p)
        self.assertTrue(etat_seance.media_reel(p))

    def test_d_pause_youtube_sans_infuse_reste_affichee(self):
        self.youtube('Playing', 10); self.now += 3; self.youtube('Playing', 13); self.now += 3
        self.youtube('Paused', 15)
        self.now += 60
        p = self.youtube('Paused', 15)
        self.assertFalse(p['stale'])
        self.assertTrue(etat_seance.media_reel(p))

    def test_pause_publiee_entre_deux_releves_n_est_pas_une_activite(self):
        # La lecture avance jusqu'à 693, la pause est publiée à 699 : les relevés suivants de cette même pause ne sont pas
        # des déplacements. Avant la 2.6.87, 693 comparé à 699 passait pour une activité et rafraîchissait l'ancienne vidéo.
        self.youtube('Playing', 690); self.now += 3; self.youtube('Playing', 693); self.now += 3
        premier = self.youtube('Paused', 699)
        for _ in range(3):
            self.now += 3
            self.assertEqual(self.youtube('Paused', 699)['changed_at'], premier['changed_at'])
        self.now += 3
        self.assertGreater(self.youtube('Paused', 740)['changed_at'], premier['changed_at'], 'Un vrai déplacement en pause compte')

    def test_infuse_qui_ne_progresse_pas_ne_date_rien(self):
        self.youtube('Playing', 10); self.now += 3; self.youtube('Playing', 13); self.youtube('Paused', 13)
        derniere = dict(self.ledger.derniere)
        for _ in range(3):
            self.now += 3; self.infuse('Playing', 100)
        self.assertEqual(self.ledger.derniere, derniere, 'Un état Infuse figé ne date aucune lecture')
        # Lot 2.6.90 : mais l'Apple TV a signalé Infuse depuis la pause YouTube : le contexte a changé.
        self.assertEqual(self.youtube('Paused', 13)['stale_reason'], 'context_changed')

    def test_generation_survit_au_redemarrage(self):
        import tempfile, os
        d = tempfile.mkdtemp(); chemin = os.path.join(d, 'observations.json')
        self.ledger = ObservationLedger(chemin, lambda: self.now)
        self.youtube('Playing', 10); self.now += 3; self.youtube('Playing', 13); self.youtube('Paused', 13)
        self.now += 10; self.infuse('Playing', 100); self.now += 6; self.infuse('Playing', 106)
        self.ledger = ObservationLedger(chemin, lambda: self.now)
        self.assertTrue(self.youtube('Paused', 13)['stale'])


if __name__ == '__main__':
    unittest.main()
