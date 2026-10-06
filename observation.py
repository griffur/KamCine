"""Sépare la simple réception d'un état pyatv de la preuve d'une vraie activité dans une app autre qu'Infuse.

Un même état Paused ou Playing republié par la connexion continue, un rappel, la ligne de commande ou une
reconnexion ne prouve rien : seule une progression de position compte. Le registre est persisté dans
observations.json pour qu'un redémarrage du service ne remette pas les compteurs à zéro, et Arrêter pose une
barrière (invalidate) au delà de laquelle une ancienne lecture ne redevient réelle qu'en progressant."""
import hashlib
import json
import os
import threading
import time
from collections import deque

MAX_RECORDS = 200


class ObservationLedger:
    def __init__(self, path=None, clock=time.time):
        self.path, self.clock = path, clock
        self.lock = threading.RLock()
        self.records = {}
        self.cutoff = 0
        # Lot 2.6.87 : la dernière vraie progression, quel que soit le lecteur (Infuse compris). Une pause d'un autre contenu
        # dont la dernière progression est antérieure est dépassée : l'ancienne vidéo YouTube mise en pause avant Reacher ne
        # redevient jamais la lecture actuelle quand on quitte Reacher. Un ordre d'événements, pas un nouveau délai.
        self.derniere = {'t': 0, 'key': None}
        self.infuse = {'key': None, 'pos': None, 'changed': 0}
        # Lot 2.6.90 : le contexte, c'est la dernière application que l'Apple TV a réellement signalée (Netflix ouvert, même sans
        # lecture, Infuse qui lit, YouTube qui reprend). Une pause dont la dernière progression est antérieure au passage à une
        # autre application n'est plus la lecture actuelle : pyatv la republie seulement. Un ordre d'événements, pas un délai.
        self.contexte = {'app': None, 't': 0}
        self.events = deque(maxlen=80)
        if path:
            try:
                with open(path) as f:
                    data = json.load(f)
                self.records = data.get('records', {})
                self.cutoff = data.get('cutoff', 0)
                self.derniere = data.get('derniere') or self.derniere
                self.contexte = data.get('contexte') or self.contexte
            except (OSError, ValueError, TypeError):
                pass

    def save(self):
        if not self.path:
            return
        try:
            fd = os.open(self.path + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump({'records': self.records, 'cutoff': self.cutoff, 'derniere': self.derniere, 'contexte': self.contexte}, f)
            os.replace(self.path + '.tmp', self.path)
        except OSError:
            pass

    def invalidate(self):
        with self.lock:
            self.cutoff = self.clock()
            for record in self.records.values():
                record['invalid'] = True
            self.events.append({'received_at': self.cutoff, 'reason': 'explicit_stop'})
            self.save()

    def observe(self, raw, source, pause_limit):
        if not raw:
            return raw
        p = dict(raw)
        if p.get('app') in (None, '', 'com.firecore.infuse'):
            with self.lock:
                self._suivre_infuse(p)
                depassee = self._contexte_depasse('com.firecore.infuse', self.infuse.get('changed', 0), p)
                if depassee:
                    p['contexte_depasse'] = self.contexte['app']
                else:
                    self._noter_contexte('com.firecore.infuse' if p.get('app') or p.get('etat') in ('Playing', 'Paused') else '', p)
            return p
        with self.lock:
            now = self.clock()
            # Jaquette, hash, durée et source varient sans que le contenu change : ils ne font pas partie de la clé.
            key = hashlib.sha256(repr((p.get('app'), p.get('titre'), p.get('serie_nom'),
                                      p.get('saison_n'), p.get('episode_n'))).encode()).hexdigest()
            record = self.records.get(key)
            pos = p.get('pos', 0)
            state = p.get('etat')
            if record is None:
                # Lot 2.6.90 : un contenu vu pour la première fois après un arrêt n'est pas une ancienne lecture, sauf s'il apparaît
                # en pause (il doit alors prouver son activité). Netflix publie Playing sans titre ni position : il ne pouvait
                # jamais progresser et restait périmé pour toujours.
                record = {'changed': now, 'pos': pos, 'state': state,
                          'invalid': bool(self.cutoff) and state == 'Paused', 'saved': now, 'last_received': now}
                self.records[key] = record
                changed = True
                # Garde seulement les contenus les plus récents : le fichier ne grossit pas indéfiniment.
                for old in sorted(self.records, key=lambda k: self.records[k].get('last_received', 0))[:-MAX_RECORDS]:
                    del self.records[old]
            else:
                # Un même libellé Paused, Idle ou Playing répété ne prouve aucune progression.
                progress = state == 'Playing' and abs(pos - record['pos']) >= 2
                paused_seek = state == 'Paused' and record['state'] == 'Paused' and abs(pos - record['pos']) >= 2
                # Lot 2.6.91 : une application qui ne publie ni position ni durée (Netflix) ne peut jamais prouver une progression.
                # Son passage observé à Playing depuis un autre état est alors la seule preuve d'un démarrage réel ; sans lui,
                # un arrêt KamCiné ancien la laissait périmée pour toujours (même clé : application, titre vide).
                demarrage = (state == 'Playing' and record['state'] not in ('Playing', None) and not pos
                             and not p.get('total'))
                changed = progress or demarrage or (paused_seek and not record['invalid'])
                if changed:
                    # avance : une vraie progression a été vue, jamais une simple première apparition (lot 2.6.82).
                    record.update(changed=now, pos=pos, invalid=False, avance=True)
                    self.derniere = {'t': now, 'key': key}
                # Lot 2.6.87 : la référence suit toujours le dernier relevé. Sinon une pause publiée à 699 après un dernier
                # relevé en lecture à 693 passait, au relevé suivant, pour un déplacement en pause : activité fantôme.
                record['pos'] = pos
                record['state'] = state
            record["last_received"] = now
            record["sources"] = record.get("sources", {})
            record["sources"][source] = record["sources"].get(source, 0) + 1
            age = max(0, now - record['changed'])
            depassee = state == 'Paused' and self.derniere['key'] != key and record['changed'] < self.derniere['t']
            contexte = state == 'Paused' and not changed and self._contexte_depasse(p.get('app'), record['changed'], p)
            stale = record['invalid'] or depassee or contexte or (state == 'Paused' and age >= pause_limit)
            if not (stale and state == 'Paused'):
                self._noter_contexte(p.get('app'), p)
            p.update(received_at=p.get('lu_a', now), changed_at=record['changed'],
                     depuis=age, stale=stale, observation_source=source, progression=bool(record.get('avance')),
                     generation=self.derniere['t'], contexte_app=self.contexte['app'],
                     stale_reason='invalidated' if record['invalid'] else 'superseded' if depassee else 'context_changed' if contexte
                     else 'unchanged_pause' if stale else '')
            if changed or record.get('stale') != stale:
                self.events.append({'key': key[:12], 'source': source, 'received_at': now,
                                    'changed_at': record['changed'], 'state': state, 'stale': stale})
            record['stale'] = stale
            if changed and now - record.get('saved', 0) >= 5 or record.get('persisted_stale') != stale:
                record.update(saved=now, persisted_stale=stale)
                self.save()
            return p

    def _suivre_infuse(self, p):
        """Une vraie progression d'Infuse (même contenu, position qui avance d'au moins 2 s en lecture) date la dernière
        lecture réelle ; une pause ou un état répété ne date rien."""
        if p.get('etat') not in ('Playing', 'Paused'):
            return
        # Lot 2.6.88 : le flux Infuse est reconnu par sa durée, pas par son titre : Infuse republie parfois le même média
        # avec un titre vide, qui reste le même flux.
        key = self._cle_infuse(p)
        with self.lock:
            pos, avant = p.get('pos', 0), self.infuse
            changed = avant.get('changed', 0) if avant['key'] == key else 0
            if avant['key'] == key and p.get('etat') == 'Playing' and avant['pos'] is not None and abs(pos - avant['pos']) >= 2:
                sauver = self.clock() - self.derniere.get('t', 0) >= 5
                changed = self.clock()
                self.derniere = {'t': changed, 'key': key}
                if sauver:
                    self.save()
            self.infuse = {'key': key, 'pos': pos, 'changed': changed}

    def _noter_contexte(self, app, p):
        """Une observation actuelle (pas une pause republiée) d'une autre application change le contexte. app vide avec un état
        Idle ou Stopped : plus aucune application ne lit."""
        app = app or ''
        if app == '' and p.get('etat') not in ('Idle', 'Stopped'):
            return
        if app != self.contexte.get('app'):
            self.contexte = {'app': app, 't': self.clock()}
            self.events.append({'received_at': self.contexte['t'], 'reason': 'context', 'app': app, 'state': p.get('etat')})

    def _contexte_depasse(self, app, progression, p):
        """Vrai pour une pause dont la dernière progression précède le passage de l'Apple TV à une autre application."""
        return (p.get('etat') == 'Paused' and self.contexte.get('app') is not None and self.contexte.get('app') != (app or '')
                and self.contexte.get('t', 0) > (progression or 0))

    @staticmethod
    def _cle_infuse(p):
        return 'infuse:%s' % int(p.get('total') or 0)

    def infuse_depassee(self, p):
        """Vrai si une autre lecture a réellement progressé après la dernière progression de ce flux Infuse : sa pause
        n'est plus la lecture actuelle (même règle que pour YouTube en 2.6.87, dans l'autre sens)."""
        key = self._cle_infuse(p)
        with self.lock:
            depuis = self.infuse.get('changed', 0) if self.infuse.get('key') == key else 0
            return self.derniere.get('key') not in (None, key) and self.derniere.get('t', 0) > depuis

    def diagnostic(self):
        with self.lock:
            return {'cutoff': self.cutoff, 'derniere_lecture': self.derniere, 'contexte': self.contexte, 'contents': len(self.records), 'events': list(self.events),
                    'records': {key[:12]: dict(value) for key, value in self.records.items()}}
