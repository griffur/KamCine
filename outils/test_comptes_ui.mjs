// Multi utilisateur (2.6.103, données personnelles, permissions et entrée par appareil en 2.6.104) : deux téléphones, deux comptes, sur le vrai service (uvicorn, Apple TV neutralisée,
// adresses externes fermées) avec des données temporaires, piloté dans Chrome sans écran. Le téléphone de Alice
// (administrateur, photo d'avant les comptes) et celui de Camille (créée par Alice) sont deux contextes séparés.
// Il faut le Python du service : KAMCINE_PY=/chemin/vers/python node outils/test_comptes_ui.mjs
// Captures dans /private/tmp/kc_comptes_*.png.
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import assert from 'node:assert/strict';

const racine=process.cwd(),PY=process.env.KAMCINE_PY||'python3';
const FERME='http://127.0.0.1:9';
const LANCEUR=`
import sys
sys.path.insert(0, sys.argv[1])
import main
main.atvlive.LIVE.demarrer = lambda: None
import uvicorn
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
`;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const portLibre=()=>new Promise(r=>{const s=createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p))})});

let service=null,port=0;
async function demarrerService(dossier){
 port=await portLibre();
 const env={...process.env,KAMCINE_APP:racine,KAMCINE_DATA:dossier,TMDB_BASE:FERME,OMDB_BASE:FERME,TRAKT_BASE:FERME,INTRODB_BASE:FERME,
  YOUTUBE_RSS:FERME,OEMBED_BASE:FERME,YOUTUBE_API_BASE:FERME,OVERSEERR_LOCAL:FERME,PYTHONWARNINGS:'ignore'};
 delete env.KAMCINE_DIR;
 service=spawn(PY,['-c',LANCEUR,racine+'/app',String(port)],{env,cwd:mkdtempSync('/private/tmp/kc-cwd-'),stdio:['ignore','pipe','pipe']});
 let sortie='';service.stdout.on('data',d=>sortie+=d);service.stderr.on('data',d=>sortie+=d);
 for(let i=0;i<100;i++){try{const r=await fetch(`http://127.0.0.1:${port}/auth/etat`);if(r.ok)return}catch(e){}
  if(service.exitCode!==null)throw new Error('Service arrêté : '+sortie);await sleep(150)}
 throw new Error('Service injoignable : '+sortie);
}

const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-pipe','--user-data-dir='+mkdtempSync('/private/tmp/kc-comptes-chrome-'),'about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let id=0,buffer='';const pending=new Map(),erreursJS=[];
chrome.stdio[4].on('data',chunk=>{buffer+=chunk.toString();let pos;while((pos=buffer.indexOf('\0'))>=0){const m=JSON.parse(buffer.slice(0,pos));buffer=buffer.slice(pos+1);if(m.id){const cb=pending.get(m.id);pending.delete(m.id);m.error?cb.reject(m.error):cb.resolve(m.result)}if(m.method==='Runtime.exceptionThrown')erreursJS.push(JSON.stringify(m.params.exceptionDetails).slice(0,400))}});
function send(method,params={},sid){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});chrome.stdio[3].write(JSON.stringify({id:n,method,params,...(sid?{sessionId:sid}:{})})+'\0')})}
async function js(sid,expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sid);if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails).slice(0,600));return r.result.value}
async function telephone(){
 const {browserContextId}=await send('Target.createBrowserContext',{},null);
 const {targetId}=await send('Target.createTarget',{url:'about:blank',browserContextId},null);
 const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true},null);
 await send('Runtime.enable',{},sessionId);await send('Page.enable',{},sessionId);await send('DOM.enable',{},sessionId);
 await taille(sessionId,390,844);return sessionId;
}
async function taille(sid,width,height){await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:2,mobile:width<700},sid);await send('Emulation.setTouchEmulationEnabled',{enabled:width<700,maxTouchPoints:5},sid)}
async function ouvrir(sid){await send('Page.navigate',{url:`http://127.0.0.1:${port}/`},sid)}
async function attendre(sid,expr,ms=8000){const fin=Date.now()+ms;while(Date.now()<fin){try{if(await js(sid,expr))return}catch(e){}await sleep(50)}throw new Error('Délai dépassé : '+expr)}
async function capture(sid,nom){const c=await send('Page.captureScreenshot',{format:'png'},sid);writeFileSync(`/private/tmp/kc_comptes_${nom}.png`,Buffer.from(c.data,'base64'))}
async function remplir(sid,champs){for(const [sel,val] of Object.entries(champs))await js(sid,`(()=>{const i=document.querySelector(${JSON.stringify(sel)});i.value=${JSON.stringify(val)};i.dispatchEvent(new Event('input',{bubbles:true}))})()`)}
const clic=(sid,sel)=>js(sid,`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)throw new Error('absent : '+${JSON.stringify(sel)});e.click();return true})()`);
const texte=(sid,sel)=>js(sid,`(document.querySelector(${JSON.stringify(sel)})||{}).textContent||''`);
const appPrete=`(document.getElementById('entree').hidden&&!document.documentElement.classList.contains('en-ouvert'))`;
async function debordement(sid,nom){assert.equal(await js(sid,'document.documentElement.scrollWidth<=innerWidth&&document.body.scrollWidth<=innerWidth'),true,'Débordement horizontal : '+nom)}
async function toastVu(sid,morceau){await attendre(sid,`document.getElementById('toast').classList.contains('show')&&document.getElementById('toast').textContent.includes(${JSON.stringify(morceau)})&&!Object.keys(OCCUPE).length`);await js(sid,`(()=>{const t=document.getElementById('toast');t.textContent='';t.classList.remove('show')})()`)}
async function compteEtSecurite(sid){
 await js(sid,`onglet('reglages');SET.cat=null;CPT.sous=null;renderSettings()`);
 await clic(sid,'[data-t="set-cat"][data-c="securite"]');
 await attendre(sid,`!!document.querySelector('.cpt-moi')`);
}
async function fichierJpeg(sid,chemin,l,h,couleur){
 const b64=await js(sid,`(()=>{const c=document.createElement('canvas');c.width=${l};c.height=${h};const x=c.getContext('2d');x.fillStyle=${JSON.stringify(couleur)};x.fillRect(0,0,${l},${h});x.fillStyle='#fff';x.fillRect(${l}/4,${h}/4,${l}/2,${h}/2);return c.toDataURL('image/jpeg',.9).split(',')[1]})()`);
 writeFileSync(chemin,Buffer.from(b64,'base64'));
}

const minuterie=setTimeout(()=>{console.error('Expiration du test');chrome.kill();service&&service.kill();process.exit(1)},240000);
const resultats=[];const ok=t=>{resultats.push(t);console.log('ok ',t)};
try{
 // ---------- Préparation : une installation 2.6.102 avec sa photo commune d'avant les comptes ----------
 const donnees=mkdtempSync('/private/tmp/kc-donnees-comptes-');
 const alice=await telephone();
 mkdirSync(donnees+'/app');
 await fichierJpeg(alice,donnees+'/app/profil.jpg',300,300,'#c0392b');
 const photoCamille=mkdtempSync('/private/tmp/kc-photo-')+'/camille.jpg';
 await fichierJpeg(alice,photoCamille,800,600,'#2e6fd8');
 const ancienne=readFileSync(donnees+'/app/profil.jpg');
 await demarrerService(donnees);
 const base=`http://127.0.0.1:${port}`;

 // Alice crée son compte administrateur (comme en 2.6.102), sa photo historique lui est rattachée.
 await ouvrir(alice);await attendre(alice,`typeof ENTREE!=='undefined'&&!!ENTREE.ecran`);
 const cree=JSON.parse(await js(alice,`(async()=>{const r=await fetch('/auth/inscription',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nom:'Alice',identifiant:'alice',mot_de_passe:'popcorn-2026'})});return await r.text()})()`));
 assert.ok(cree.ok&&cree.compte.avatar_v,'photo historique rattachée au premier administrateur');
 await ouvrir(alice);await attendre(alice,appPrete,10000);
 await attendre(alice,`document.getElementById('hd-img').getAttribute('src')==='/comptes/1/avatar?v='+AUTH.compte.avatar_v&&!document.getElementById('hd-img').classList.contains('hidden')`);
 ok('Alice : photo historique rattachée à son compte, affichée dans l’en tête');

 // ---------- A. Alice ajoute Camille ----------
 await compteEtSecurite(alice);
 assert.equal(await js(alice,`document.querySelectorAll('#cpt-liste .cpt-ligne').length`),1);
 assert.match(await texte(alice,'.cpt-ajout'),/Ajouter un utilisateur/);
 assert.doesNotMatch(await texte(alice,'#reglages-body'),/Bientôt/);
 await clic(alice,'[data-t="cpt-ajouter"]');
 await attendre(alice,`!!document.getElementById('f-ajout')`);
 await js(alice,`window.__marque=1`);
 assert.equal(await js(alice,`document.querySelector('.cf-role[data-v="utilisateur"]').getAttribute('aria-checked')`),'true','Utilisateur par défaut');
 assert.match(await texte(alice,'#f-ajout'),/Utilise KamCiné avec son profil personnel\./);
 assert.match(await texte(alice,'#f-ajout'),/Peut également gérer cette installation KamCiné, ses utilisateurs et ses services\./);
 assert.equal(await js(alice,`getComputedStyle(document.querySelector('#f-ajout .en-cta')).backgroundColor`),'rgb(52, 195, 143)','bouton vert');
 for(const w of [320,375,390]){await taille(alice,w,700);await sleep(80);await debordement(alice,'ajout '+w);await capture(alice,'ajout_'+w)}
 await taille(alice,390,844);
 // Validation locale : erreurs sous les champs.
 await clic(alice,'#f-ajout .en-cta');
 assert.equal(await texte(alice,'#err-in-a-nom'),'Indiquez un prénom.');
 assert.match(await texte(alice,'#err-in-a-id'),/3 à 32 caractères/);
 assert.equal(await js(alice,`document.activeElement.id`),'in-a-nom');
 await remplir(alice,{'#in-a-nom':'Camille','#in-a-id':'camille','#in-a-mdp':'cinema-camille','#in-a-mdp2':'cinema-autre'});
 assert.equal(await texte(alice,'#err-in-a-nom'),'','erreur effacée à la saisie');
 await clic(alice,'#f-ajout .en-cta');
 assert.equal(await texte(alice,'#err-in-a-mdp2'),'Les deux mots de passe sont différents.');
 // Identifiant déjà pris : l'erreur du service revient sous le champ.
 await remplir(alice,{'#in-a-id':'Alice','#in-a-mdp2':'cinema-camille'});
 await clic(alice,'#f-ajout .en-cta');
 await attendre(alice,`document.getElementById('err-in-a-id').textContent.includes('déjà pris')`);
 await capture(alice,'ajout_erreur');
 ok('Ajouter un utilisateur : Utilisateur par défaut, explications, bouton vert, erreurs sous les champs, identifiant unique');
 // Entrée passe au champ suivant.
 await js(alice,`document.getElementById('in-a-nom').focus()`);
 await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},alice);
 assert.equal(await js(alice,`document.activeElement.id`),'in-a-id');
 await remplir(alice,{'#in-a-id':'camille'});
 await clic(alice,'#f-ajout .en-cta');
 await toastVu(alice,'Compte de Camille créé');
 await attendre(alice,`document.querySelectorAll('#cpt-liste .cpt-ligne').length===2`);
 assert.equal(await js(alice,`window.__marque`),1,'aucun rechargement de la page');
 assert.match(await texte(alice,'#cpt-liste'),/Camille\s*Utilisateur/);
 await capture(alice,'liste');
 ok('A. Alice crée Camille sans rechargement, la liste montre Alice et Camille avec leurs rôles');

 // ---------- L, O. Téléphone neuf de Camille : présentation, connexion, bienvenue une fois ----------
 const camille=await telephone();
 await ouvrir(camille);
 await attendre(camille,`ENTREE.ecran==='en-intro'`,10000);
 await capture(camille,'intro_nouvel_appareil');
 await clic(camille,'#en-passer');
 await attendre(camille,`ENTREE.ecran==='en-connexion'`);
 assert.equal(await js(camille,`localStorage.getItem('kc_presentation_vue')`),'1','présentation notée pour cet appareil');
 await remplir(camille,{'#in-l-id':'camille','#in-l-mdp':'cinema-camille'});
 await clic(camille,'#f-connexion .en-cta');
 await attendre(camille,`ENTREE.ecran==='en-bienvenue'`,10000);
 assert.equal(await texte(camille,'#en-bienvenue-titre'),'Bienvenue Camille 👋');
 assert.match(await texte(camille,'#en-bienvenue'),/Votre profil KamCiné est prêt\.[\s\S]*Découvrez et demandez vos films et séries[\s\S]*Créez vos favoris[\s\S]*Profitez des séances KamCiné/);
 for(const w of [320,375,390]){await taille(camille,w,w===320?568:844);await sleep(500);await debordement(camille,'bienvenue '+w);await capture(camille,'bienvenue_'+w)}
 await taille(camille,390,844);
 await clic(camille,'#en-decouvrir');
 await attendre(camille,appPrete,10000);
 assert.equal(await js(camille,`AUTH.compte.identifiant+'|'+AUTH.compte.bienvenue`),'camille|false');
 ok('L, O. Téléphone neuf : splash, présentation, connexion, puis « Bienvenue Camille 👋 » et Découvrir KamCiné');
 // N. Session valable : ni présentation ni bienvenue au lancement suivant.
 await ouvrir(camille);
 const ecrans=[];for(let i=0;i<30;i++){ecrans.push(await js(camille,`typeof ENTREE==='undefined'?null:ENTREE.ecran`));if(await js(camille,appPrete).catch(()=>false))break;await sleep(100)}
 await attendre(camille,appPrete,10000);
 assert.ok(!ecrans.includes('en-intro')&&!ecrans.includes('en-bienvenue'),'aucun écran d’accueil : '+ecrans.join(','));
 ok('N. Session valable : KamCiné directement, sans présentation ni bienvenue');
 // M, P. Un autre téléphone qui a déjà vu la présentation : connexion directe ; seconde connexion de Camille : plus de bienvenue.
 const autre=await telephone();
 await ouvrir(autre);await attendre(autre,`ENTREE.ecran==='en-intro'`,10000);
 await js(autre,`localStorage.setItem('kc_presentation_vue','1')`);
 await ouvrir(autre);await attendre(autre,`ENTREE.ecran==='en-connexion'`,10000);
 await remplir(autre,{'#in-l-id':'camille','#in-l-mdp':'cinema-camille'});
 await clic(autre,'#f-connexion .en-cta');
 await attendre(autre,appPrete,10000);
 ok('M, P. Appareil ayant déjà vu la présentation : connexion directe ; seconde connexion de Camille sans bienvenue');

 // ---------- C, D. Alice reste connecté ----------
 await ouvrir(alice);await attendre(alice,appPrete,10000);
 assert.equal(await js(alice,`AUTH.compte.identifiant`),'alice');
 const [cm,cl]=await Promise.all([send('Network.getCookies',{urls:[base]},alice).catch(()=>null),send('Network.getCookies',{urls:[base]},camille).catch(()=>null)]);
 if(cm&&cl&&cm.cookies.length&&cl.cookies.length)assert.notEqual(cm.cookies[0].value,cl.cookies[0].value);
 assert.equal(await js(camille,`document.cookie.includes('kc_compte')`),false,'cookie HttpOnly, jamais lisible par la page');
 ok('C, D. Alice reste connecté en même temps, deux sessions séparées, cookie HttpOnly');

 // ---------- E, F, G. Camille : son nom, avatar neutre, jamais la photo de Alice ----------
 assert.equal(await texte(camille,'#hd-ini'),'C');
 assert.equal(await js(camille,`document.getElementById('hd-img').classList.contains('hidden')&&!document.getElementById('hd-img').getAttribute('src')`),true);
 assert.equal(await js(camille,`document.getElementById('hd-profil').classList.contains('av-def')`),true,'dégradé avec initiale');
 await js(camille,`onglet('profil')`);await attendre(camille,`!!document.querySelector('#profil-body .pf-av .av')`);
 assert.equal(await js(camille,`document.getElementById('in-nom').value`),'Camille');
 assert.equal(await js(camille,`document.querySelector('#profil-body .pf-av img')`),null);
 assert.match(await texte(camille,'.pf-compte'),/camille · Utilisateur/);
 assert.equal(await js(camille,`performance.getEntriesByType('resource').filter(e=>/avatar|profil\\.jpg/.test(e.name)).length`),0,'aucune photo demandée');
 assert.equal(await js(camille,`fetch('/comptes/1/avatar').then(r=>r.status)`),404,'la photo de Alice ne lui est pas servie');
 assert.equal(await js(camille,`fetch('/profil.jpg').then(r=>r.status)`),404);
 await capture(camille,'profil_neutre');
 ok('E, F, G. Camille voit son nom, son initiale sur fond coloré, jamais la photo de Alice');

 // ---------- L. Réglages d'un utilisateur ----------
 await js(camille,`onglet('reglages');SET.cat=null;renderSettings()`);
 const cats=await js(camille,`[...document.querySelectorAll('[data-t="set-cat"]')].map(e=>e.dataset.c).join(',')`);
 assert.equal(cats,'apropos,apparence,securite');
 assert.match(await texte(camille,'#reglages-body'),/gérés par un administrateur/);
 await js(camille,`SET.q='tmdb';renderSettings();majRecherche()`);
 assert.match(await texte(camille,'#set-res'),/Aucun réglage/,'la recherche ne montre pas les réglages réservés');
 await js(camille,`SET.q='';SET.cat='services';renderSettings()`);
 assert.equal(await js(camille,`SET.cat`),null,'catégorie réservée refusée même par un accès direct');
 await js(camille,`SET.cat='apropos';renderSettings()`);
 assert.equal(await js(camille,`!!document.querySelector('[data-t="reset"]')`),false);
 await js(camille,`SET.cat='apparence';renderSettings()`);
 assert.equal(await js(camille,`!!document.querySelector('[data-t="ic"]')`),false);
 await compteEtSecurite(camille);
 assert.equal(await js(camille,`!!document.getElementById('cpt-liste')||!!document.querySelector('[data-t="cpt-ajouter"]')||!!document.querySelector('[data-k="verrou_duree"]')`),false);
 // A, B. Appareils : page d'administration de l'installation.
 assert.equal(await js(camille,`getComputedStyle(document.getElementById('t-appareils')).display`),'none','onglet Appareils absent pour Camille');
 await js(camille,`onglet('appareils')`);await sleep(100);
 assert.equal(await js(camille,`VUE`),'seance','accès direct à Appareils refusé');
 assert.equal(await js(camille,`fetch('/appareils/etat').then(r=>r.status)`),403);
 assert.notEqual(await js(alice,`getComputedStyle(document.getElementById('t-appareils')).display`),'none');
 await js(alice,`onglet('appareils')`);await sleep(100);
 assert.equal(await js(alice,`VUE==='appareils'&&!!document.querySelector('#appareils-body #amp-ip')`),true,'toujours là pour un administrateur');
 await capture(camille,'navigation_utilisatrice');
 for(const w of [320,375,390]){await taille(camille,w,700);await js(camille,`onglet('reglages');SET.cat=null;renderSettings()`);await sleep(60);await debordement(camille,'réglages utilisateur '+w);await capture(camille,'reglages_'+w)}
 await taille(camille,390,844);
 ok('A, B, C. Appareils visible pour Alice seulement (403 pour Camille) ; Réglages réservés masqués pour Camille');

 // ---------- K. API d'administration : 403 depuis le navigateur de Camille ----------
 const statuts=await js(camille,`Promise.all([['/comptes','GET'],['/comptes','POST'],['/comptes/2/role','POST'],['/comptes/1/activation','POST'],['/reglages','POST'],['/tmdb/cle','POST'],['/reglages/raccourci','GET']]
  .map(([u,m])=>fetch(u,{method:m,headers:{'Content-Type':'application/json'},body:m==='POST'?JSON.stringify({role:'admin',actif:false,nom:'X',identifiant:'xavier',mot_de_passe:'motdepasse',recul:9,cle:'x'.repeat(32)}):undefined}).then(r=>r.status)))`);
 assert.deepEqual(statuts,[403,403,403,403,403,403,403]);
 ok('K. Chaque API d’administration répond 403 à Camille');

 // ---------- I, H. Camille change son nom et sa photo (aperçu, remplacement) ----------
 await compteEtSecurite(camille);
 await clic(camille,'[data-t="cpt-profil"]');
 await attendre(camille,`!!document.getElementById('f-profil')`);
 await remplir(camille,{'#in-p-nom':'Camille D.'});
 await clic(camille,'#f-profil .en-cta');
 await toastVu(camille,'Prénom enregistré');
 assert.equal(await js(camille,`AUTH.compte.nom`),'Camille D.');
 const {root}=await send('DOM.getDocument',{},camille);
 const {nodeId}=await send('DOM.querySelector',{nodeId:root.nodeId,selector:'#file'},camille);
 await send('DOM.setFileInputFiles',{nodeId,files:[photoCamille]},camille);
 await attendre(camille,`!!CPT.apercu&&!!document.querySelector('.cpt-tete .av img[src^="blob:"]')`);
 assert.match(await texte(camille,'.cpt-photo-actions'),/Enregistrer la photo/);
 assert.equal(await js(camille,`AUTH.compte.avatar_v`),null,'rien d’envoyé avant Enregistrer');
 await capture(camille,'apercu');
 await clic(camille,'[data-t="cpt-photo-ok"]');
 await toastVu(camille,'Photo enregistrée');
 await attendre(camille,`AUTH.compte.avatar_v&&document.getElementById('hd-img').getAttribute('src')==='/comptes/2/avatar?v='+AUTH.compte.avatar_v&&!document.getElementById('hd-img').classList.contains('hidden')`);
 const dims=await js(camille,`new Promise(r=>{const i=new Image();i.onload=()=>r([i.naturalWidth,i.naturalHeight]);i.src='/comptes/2/avatar?v='+AUTH.compte.avatar_v})`);
 assert.deepEqual(dims,[512,512],'recadrée en carré de 512 px');
 const avant=await js(camille,`AUTH.compte.avatar_v`);
 await send('DOM.setFileInputFiles',{nodeId:(await send('DOM.querySelector',{nodeId:(await send('DOM.getDocument',{},camille)).root.nodeId,selector:'#file'},camille)).nodeId,files:[donnees+'/app/profil.jpg']},camille);
 await attendre(camille,`!!CPT.apercu`);
 await clic(camille,'[data-t="cpt-photo-annuler"]');
 assert.equal(await js(camille,`CPT.apercu===null&&AUTH.compte.avatar_v===${avant}`),true,'Annuler garde la photo');
 await capture(camille,'profil_photo');
 await js(camille,`onglet('profil')`);await attendre(camille,`!!document.querySelector('#profil-body .pf-av img')`);
 assert.equal(await js(camille,`document.getElementById('in-nom').value`),'Camille D.');
 ok('I, H. Camille change son nom et sa photo : aperçu, Enregistrer, Annuler, 512 px, en tête et Profil à jour');
 // Alice ne voit rien changer chez lui.
 await ouvrir(alice);await attendre(alice,appPrete,10000);
 assert.equal(await js(alice,`AUTH.compte.nom+'|'+document.getElementById('hd-img').getAttribute('src')`),'Alice|/comptes/1/avatar?v='+cree.compte.avatar_v);
 assert.deepEqual([...Buffer.from(await js(alice,`fetch('/comptes/1/avatar').then(r=>r.arrayBuffer()).then(b=>[...new Uint8Array(b)])`))],[...ancienne]);
 ok('Le profil de Alice est inchangé, sa photo reste la photo historique');

 // ---------- J. Alice change le rôle de Camille ----------
 await compteEtSecurite(alice);
 await clic(alice,'.cpt-ligne[data-id="2"]');
 await attendre(alice,`!!document.querySelector('[data-t="cpt-role"][data-v="admin"]')`);
 assert.match(await texte(alice,'.cpt-tete'),/Camille D\./);
 assert.equal(await js(alice,`!!document.querySelector('.cpt-tete .av img')`),true,'l’administrateur voit la photo de Camille');
 for(const w of [320,375,390]){await taille(alice,w,700);await sleep(60);await debordement(alice,'fiche '+w);await capture(alice,'fiche_'+w)}
 await taille(alice,390,844);
 await clic(alice,'[data-t="cpt-role"][data-v="admin"]');
 await toastVu(alice,'est administrateur');
 assert.equal(await js(camille,`fetch('/comptes').then(r=>r.status)`),200,'droits appliqués tout de suite côté service');
 await clic(alice,'[data-t="cpt-role"][data-v="utilisateur"]');
 await toastVu(alice,'est utilisateur');
 assert.equal(await js(camille,`fetch('/comptes').then(r=>r.status)`),403);
 ok('J. Alice fait passer Camille administratrice puis utilisatrice, droits appliqués tout de suite');

 // Réinitialisation du mot de passe par l'administrateur.
 await remplir(alice,{'#in-r-mdp':'nouveau-camille','#in-r-mdp2':'nouveau-camille'});
 await clic(alice,'#f-reinit .en-cta');
 await toastVu(alice,'Mot de passe réinitialisé');
 await js(camille,`chargerTout()`);
 await attendre(camille,`ENTREE.ecran==='en-connexion'&&!document.getElementById('entree').hidden`);
 await remplir(camille,{'#in-l-id':'camille','#in-l-mdp':'nouveau-camille'});
 await clic(camille,'#f-connexion .en-cta');
 await attendre(camille,appPrete,10000);
 assert.equal(await js(camille,`AUTH.compte.nom`),'Camille D.');
 ok('Réinitialisation : Camille est déconnectée puis entre avec le nouveau mot de passe');

 // ---------- N. Dernier administrateur protégé ----------
 await js(alice,`fermerSousCompte()`);
 await clic(alice,'.cpt-ligne[data-id="1"]');
 await attendre(alice,`!!document.querySelector('[data-t="cpt-role"][data-v="utilisateur"]')`);
 await clic(alice,'[data-t="cpt-role"][data-v="utilisateur"]');
 await attendre(alice,`!document.getElementById('modal').classList.contains('hidden')`);
 await clic(alice,'#m-oui');
 await toastVu(alice,'dernier administrateur actif');
 assert.equal(await js(alice,`AUTH.compte.admin&&document.querySelector('[data-t="cpt-role"][data-v="admin"]').classList.contains('on')`),true);
 await clic(alice,'[data-t="cpt-desactiver"]');
 await attendre(alice,`!document.getElementById('modal').classList.contains('hidden')`);
 await clic(alice,'#m-oui');
 await toastVu(alice,'dernier administrateur actif');
 assert.equal(await js(alice,appPrete),true,'toujours connecté');
 ok('N. Le dernier administrateur actif ne peut ni perdre son rôle ni être désactivé');

 // ---------- M. Désactivation de Camille : sessions invalidées ----------
 await js(alice,`fermerSousCompte()`);
 await clic(alice,'.cpt-ligne[data-id="2"]');
 await attendre(alice,`!!document.querySelector('[data-t="cpt-desactiver"]')`);
 await clic(alice,'[data-t="cpt-desactiver"]');
 await attendre(alice,`!document.getElementById('modal').classList.contains('hidden')`);
 assert.match(await texte(alice,'#m-texte'),/Camille D\. ne pourra plus se connecter/);
 await clic(alice,'#m-oui');
 await toastVu(alice,'Compte désactivé');
 await attendre(alice,`!!document.querySelector('[data-t="cpt-reactiver"]')`);
 await js(camille,`api('/catalogue/recents')`);
 await attendre(camille,`ENTREE.ecran==='en-connexion'&&!document.getElementById('entree').hidden`);
 await remplir(camille,{'#in-l-id':'camille','#in-l-mdp':'nouveau-camille'});
 await clic(camille,'#f-connexion .en-cta');
 await attendre(camille,`document.getElementById('al-connexion').textContent.includes('incorrect')`);
 await js(alice,`fermerSousCompte()`);
 assert.match(await texte(alice,'#cpt-liste'),/désactivé/);
 await capture(alice,'liste_desactive');
 await js(alice,`CPT.id=2;ouvrirSousCompte('compte')`);
 await clic(alice,'[data-t="cpt-reactiver"]');
 await toastVu(alice,'Compte réactivé');
 assert.equal(await js(camille,`document.getElementById('in-l-mdp').value`),'','mot de passe effacé après un refus');
 await remplir(camille,{'#in-l-mdp':'nouveau-camille'});
 await clic(camille,'#f-connexion .en-cta');
 await attendre(camille,appPrete,10000);
 ok('M. Camille désactivée : déconnectée sur son téléphone, connexion refusée ; réactivée, elle entre de nouveau');

 // ---------- E, F, G, K. Favoris et Profil personnels ----------
 const fl=await js(camille,`api('/favoris/basculer',POST({type:'movie',id:27205,titre:'Inception',annee:'2010'})).then(r=>r.j.favori)`);
 assert.equal(fl,true);
 await js(camille,`onglet('profil')`);await attendre(camille,`!!document.querySelector('#pf-contenu .sec-title')`,15000);
 const pl=await texte(camille,'#pf-contenu');
 assert.match(pl,/Mon activité[\s\S]*Films favoris[\s\S]*Inception[\s\S]*La salle · commune à l’installation/);
 assert.match(pl,/Toute l’installation, quel que soit le compte/);
 await capture(camille,'profil_personnel');
 await js(alice,`onglet('profil')`);await attendre(alice,`!!document.querySelector('#pf-contenu .sec-title')`,15000);
 assert.doesNotMatch(await texte(alice,'#pf-contenu'),/Inception/,'le favori de Camille n’arrive pas chez Alice');
 ok('E, F, G, K. Favoris personnels (Camille part de zéro, son favori reste chez elle), salle présentée comme commune');

 // ---------- Q, R, S, T. Permissions de Camille, réglées dans sa fiche ----------
 await compteEtSecurite(alice);
 await clic(alice,'.cpt-ligne[data-id="2"]');
 await attendre(alice,`document.querySelectorAll('[data-t="cpt-perm"]').length===3`);
 assert.equal(await js(alice,`[...document.querySelectorAll('[data-t="cpt-perm"]')].every(b=>b.getAttribute('aria-checked')==='true')`),true,'permissions normales par défaut');
 await capture(alice,'permissions');
 for(const p of ['demander','seances','telechargements']){await clic(alice,`[data-t="cpt-perm"][data-v="${p}"]`);await toastVu(alice,'retiré')}
 assert.equal(await js(alice,`[...document.querySelectorAll('[data-t="cpt-perm"]')].every(b=>b.getAttribute('aria-checked')==='false')`),true);
 await js(camille,`chargerTout()`);
 await attendre(camille,`document.documentElement.classList.contains('sans-seances')&&document.documentElement.classList.contains('sans-demander')&&document.documentElement.classList.contains('sans-telechargements')`);
 assert.equal(await js(camille,`(()=>{const b=document.createElement('button');b.dataset.t='fi-seance';const d=document.createElement('button');d.dataset.t='fi-demander';document.body.append(b,d);const r=[getComputedStyle(b).display,getComputedStyle(d).display,getComputedStyle(document.getElementById('btn-go')).display,getComputedStyle(document.getElementById('t-telechargements')).display];b.remove();d.remove();return r.join(',')})()`),'none,none,none,none');
 assert.deepEqual(await js(camille,`Promise.all([fetch('/start',{method:'POST'}),fetch('/overseerr/demander?id=1',{method:'POST'}),fetch('/telechargements')].map(p=>p.then(r=>r.status)))`),[403,403,403]);
 const refus=await js(camille,`api('/start',{method:'POST'}).then(r=>r.j.message)`);
 assert.match(refus,/Un administrateur de l'installation peut te l'autoriser/);
 assert.notEqual(await js(alice,`fetch('/telechargements').then(r=>r.status)`),403,'Alice garde tous les droits');
 await capture(camille,'sans_permissions');
 for(const p of ['demander','seances','telechargements']){await clic(alice,`[data-t="cpt-perm"][data-v="${p}"]`);await toastVu(alice,'autorisé')}
 await js(camille,`chargerTout()`);
 await attendre(camille,`!document.documentElement.classList.contains('sans-seances')`);
 assert.equal(await js(camille,`fetch('/planning/annuler',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.status)`)!==403,true,'D. séance de nouveau permise');
 await clic(alice,'[data-t="cpt-role"][data-v="admin"]');await toastVu(alice,'est administrateur');
 assert.match(await texte(alice,'#reglages-body'),/Administrateur de l’installation/);
 assert.equal(await js(alice,`[...document.querySelectorAll('[data-t="cpt-perm"]')].every(b=>b.disabled&&b.getAttribute('aria-checked')==='true')`),true,'accordées d’office');
 await clic(alice,'[data-t="cpt-role"][data-v="utilisateur"]');await toastVu(alice,'est utilisateur');
 ok('Q, R, S, T. Permissions retirées : actions masquées et API 403 ; rétablies ; administrateur : toutes accordées d’office');

 // ---------- Thème clair : formulaires lisibles ----------
 await js(alice,`document.documentElement.setAttribute('data-theme','light');ouvrirSousCompte('ajout')`);
 const couleurs=await js(alice,`(()=>{const i=getComputedStyle(document.getElementById('in-a-nom'));return [i.color,i.backgroundColor]})()`);
 assert.equal(couleurs[0],'rgb(0, 0, 0)');
 await capture(alice,'ajout_clair');
 await js(alice,`document.documentElement.setAttribute('data-theme','dark');fermerSousCompte()`);
 ok('Thème clair : champs lisibles (texte sombre sur fond clair)');

 assert.deepEqual(erreursJS,[],'Erreurs JavaScript : '+erreursJS.join('\n'));
 console.log('\n'+resultats.length+' vérifications réussies');
}catch(e){console.error('ÉCHEC',e);process.exitCode=1}
finally{clearTimeout(minuterie);chrome.kill();if(service)service.kill()}
