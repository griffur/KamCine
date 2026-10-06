// Entrée dans KamCiné (2.6.102, présentation par appareil en 2.6.104, bienvenue de l'administrateur en 2.6.105) : splash, présentation, ancien code, création du compte, connexion, déconnexion.
// Lance le vrai service (uvicorn, Apple TV neutralisée, adresses externes fermées) avec des données temporaires, puis pilote
// Chrome sans écran. Il faut le Python du service : KAMCINE_PY=/chemin/vers/python node outils/test_entree_ui.mjs
// Captures dans /private/tmp/kc_entree_*.png.
import {mkdtempSync,writeFileSync,existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {pbkdf2Sync,randomBytes} from 'node:crypto';
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

let service=null,port=0,donnees='';
async function demarrerService(dossier){
 donnees=dossier;port=await portLibre();
 const env={...process.env,KAMCINE_APP:racine,KAMCINE_DATA:dossier,TMDB_BASE:FERME,OMDB_BASE:FERME,TRAKT_BASE:FERME,INTRODB_BASE:FERME,
  YOUTUBE_RSS:FERME,OEMBED_BASE:FERME,YOUTUBE_API_BASE:FERME,OVERSEERR_LOCAL:FERME,PYTHONWARNINGS:'ignore'};
 delete env.KAMCINE_DIR;
 service=spawn(PY,['-c',LANCEUR,racine+'/app',String(port)],{env,cwd:mkdtempSync('/private/tmp/kc-cwd-'),stdio:['ignore','pipe','pipe']});
 let sortie='';service.stdout.on('data',d=>sortie+=d);service.stderr.on('data',d=>sortie+=d);
 for(let i=0;i<100;i++){try{const r=await fetch(`http://127.0.0.1:${port}/auth/etat`);if(r.ok)return}catch(e){}
  if(service.exitCode!==null)throw new Error('Service arrêté : '+sortie);await sleep(150)}
 throw new Error('Service injoignable : '+sortie);
}
async function arreterService(){if(!service)return;service.kill();await new Promise(r=>service.once('exit',r));service=null}
function ancienCode(dossier,code){
 const sel=randomBytes(16).toString('hex');
 writeFileSync(dossier+'/auth.json',JSON.stringify({sel,hash:pbkdf2Sync(code,Buffer.from(sel,'hex'),200000,32,'sha256').toString('hex'),longueur:code.length}));
}

const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-pipe','--user-data-dir='+mkdtempSync('/private/tmp/kc-entree-chrome-'),'about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let id=0,buffer='',session;const pending=new Map(),erreursJS=[];
chrome.stdio[4].on('data',chunk=>{buffer+=chunk.toString();let pos;while((pos=buffer.indexOf('\0'))>=0){const m=JSON.parse(buffer.slice(0,pos));buffer=buffer.slice(pos+1);if(m.id){const cb=pending.get(m.id);pending.delete(m.id);m.error?cb.reject(m.error):cb.resolve(m.result)}if(m.method==='Runtime.exceptionThrown')erreursJS.push(JSON.stringify(m.params.exceptionDetails).slice(0,400));if(m.method==='Fetch.requestPaused'&&intercepter)intercepter(m.params,m.sessionId)}});
let intercepter=null;
function send(method,params={},sid=session){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});chrome.stdio[3].write(JSON.stringify({id:n,method,params,...(sid?{sessionId:sid}:{})})+'\0')})}
async function js(expression,sid=session){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,replMode:true},sid);if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails).slice(0,600));return r.result.value}
async function onglet(contexte){
 const {targetId}=await send('Target.createTarget',{url:'about:blank',...(contexte?{browserContextId:contexte}:{})},null);
 const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true},null);
 await send('Runtime.enable',{},sessionId);await send('Page.enable',{},sessionId);return sessionId;
}
async function taille(width,height,sid=session){await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:2,mobile:width<700},sid);await send('Emulation.setTouchEmulationEnabled',{enabled:width<700,maxTouchPoints:5},sid)}
async function ouvrir(sid=session){await send('Page.navigate',{url:`http://127.0.0.1:${port}/`},sid)}
async function attendre(expr,ms=6000,sid=session){const fin=Date.now()+ms;while(Date.now()<fin){try{if(await js(expr,sid))return}catch(e){}await sleep(50)}throw new Error('Délai dépassé : '+expr)}
async function capture(nom,sid=session){const c=await send('Page.captureScreenshot',{format:'png'},sid);writeFileSync(`/private/tmp/kc_entree_${nom}.png`,Buffer.from(c.data,'base64'))}
const ecran=`ENTREE.ecran`;
const appVisible=`(document.getElementById('entree').hidden&&!document.documentElement.classList.contains('en-ouvert'))`;
// Rien de l'app n'est visible tant que l'entrée est là : le point central de l'écran appartient à l'entrée.
const couvre=`(()=>{const e=document.getElementById('entree');if(e.hidden)return false;const s=getComputedStyle(e);const x=document.elementFromPoint(innerWidth/2,innerHeight/2);return s.display!=='none'&&parseFloat(s.opacity)>.98&&!!x&&e.contains(x)})()`;
async function glisser(dx,sid=session){
 const y=400,x0=dx<0?300:80;
 await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x0,y}]},sid);
 for(let i=1;i<=6;i++){await send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x0+dx*i/6,y}]},sid);await sleep(16)}
 await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]},sid);await sleep(560);
}
async function debordement(nom,sid=session){assert.equal(await js('document.documentElement.scrollWidth<=innerWidth&&document.getElementById("entree").scrollWidth<=innerWidth',sid),true,'Débordement horizontal : '+nom)}
async function remplir(champs,sid=session){for(const [sel,val] of Object.entries(champs))await js(`(()=>{const i=document.querySelector(${JSON.stringify(sel)});i.value=${JSON.stringify(val)};i.dispatchEvent(new Event('input',{bubbles:true}))})()`,sid)}
async function etatService(cookie=''){const r=await fetch(`http://127.0.0.1:${port}/auth/etat`,{headers:cookie?{cookie}:{}});return r.json()}

const minuterie=setTimeout(()=>{console.error('Expiration du test');chrome.kill();service&&service.kill();process.exit(1)},180000);
const resultats=[];const ok=t=>{resultats.push(t);console.log('ok ',t)};
try{
 // ---------- A. Première installation ----------
 await demarrerService(mkdtempSync('/private/tmp/kc-donnees-neuves-'));
 session=await onglet();await taille(390,844);
 await ouvrir();
 const echantillons=[];
 for(let i=0;i<14;i++){await sleep(60);echantillons.push(await js(couvre));if(i===7)await capture('splash_390')}
 assert.ok(echantillons.every(Boolean),'La Home ne doit jamais apparaître pendant le splash');
 await attendre(`${ecran}==='en-intro'`);
 ok('splash puis présentation, sans flash de la Home');
 await sleep(700);await capture('intro1_390');await debordement('intro 390');
 assert.equal(await js(`document.getElementById('en-suivant').textContent`),'Continuer');
 await glisser(-220);assert.equal(await js('ENTREE.page'),1,'swipe vers la page 2');await capture('intro2_390');
 await glisser(200);assert.equal(await js('ENTREE.page'),0,'swipe retour page 1');
 await glisser(160);assert.equal(await js('ENTREE.page'),0,'résistance au bord gauche');
 ok('swipe horizontal (avant, arrière, bord)');
 await js(`document.getElementById('en-suivant').click()`);await sleep(560);assert.equal(await js('ENTREE.page'),1);
 await js(`document.querySelectorAll('#en-points button')[3].click()`);await sleep(600);
 assert.equal(await js('ENTREE.page'),3);assert.equal(await js(`document.getElementById('en-suivant').textContent`),'Commencer');
 assert.equal(await js(`document.getElementById('en-passer').classList.contains('cache')`),true,'Passer masqué sur la dernière page');
 await capture('intro4_390');
 await js(`document.querySelectorAll('#en-points button')[2].click()`);await sleep(560);await capture('intro3_390');
 await js(`document.querySelectorAll('#en-points button')[0].click()`);await sleep(560);
 ok('boutons Continuer, points de page, Commencer');
 // Présentation non terminée : elle revient au rechargement.
 await ouvrir();await attendre(`${ecran}==='en-intro'`);
 await js(`document.getElementById('en-passer').click()`);
 await attendre(`${ecran}==='en-compte'`);await sleep(600);await capture('compte_390');await debordement('compte 390');
 ok('Passer mène à la création du compte');
 await ouvrir();await attendre(`${ecran}!==null`);
 assert.equal(await js(ecran),'en-compte','présentation terminée : elle ne revient pas');
 ok('état de présentation persistant');
 // Création : validations locales.
 await sleep(500);
 await js(`document.querySelector('#f-compte .en-cta').click()`);await sleep(100);
 assert.equal(await js(`document.getElementById('in-c-nom').getAttribute('aria-invalid')`),'true');
 await remplir({'#in-c-nom':'Alex','#in-c-id':'Alex.B','#in-c-mdp':'popcorn-2026','#in-c-mdp2':'popcorn-2027'});
 await js(`document.querySelector('#f-compte .en-cta').click()`);await sleep(100);
 assert.match(await js(`document.getElementById('err-c-mdp2').textContent`),/différents/);
 await capture('compte_erreurs_390');
 await remplir({'#in-c-mdp':'court','#in-c-mdp2':'court'});
 await js(`document.querySelector('#f-compte .en-cta').click()`);await sleep(100);
 assert.match(await js(`document.getElementById('err-c-mdp').textContent`),/8 caractères/);
 await js(`document.querySelector('[data-oeil=in-c-mdp]').click()`);
 assert.equal(await js(`document.getElementById('in-c-mdp').type`),'text','afficher le mot de passe');
 await js(`document.querySelector('[data-oeil=in-c-mdp]').click()`);
 ok('erreurs de saisie et affichage du mot de passe');
 await remplir({'#in-c-mdp':'popcorn-2026','#in-c-mdp2':'popcorn-2026'});
 await js(`document.getElementById('in-c-nom').focus();document.getElementById('in-c-nom').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))`);
 assert.equal(await js('document.activeElement.id'),'in-c-id','Entrée passe au champ suivant');
 assert.equal(await js(`document.querySelector('#f-compte .en-cta').classList.contains('charge')`),false,'pas d’envoi avant le dernier champ');
 ok('clavier : Entrée passe au champ suivant');
 await js(`document.querySelector('#f-compte .en-cta').click()`);
 await sleep(40);assert.equal(await js(`document.querySelector('#f-compte .en-cta').classList.contains('charge')`),true,'état de chargement');
 // 2.6.105 : premier compte d'une installation neuve : bienvenue de l'administrateur, puis Configurer KamCiné ou Plus tard.
 await attendre(`${ecran}==='en-bienvenue-admin'`,8000);
 await js(`document.getElementById('en-plus-tard').click()`);
 await attendre(appVisible,8000);
 assert.equal(await js('AUTH.compte.identifiant'),'alex.b');assert.equal(await js('AUTH.compte.admin'),true);
 assert.equal(await js(`document.cookie.includes('kc_compte')`),false,'cookie HttpOnly');
 const cookies=(await send('Network.getAllCookies',{},session).catch(()=>null))||(await send('Storage.getCookies',{},null));
 const ck=(cookies.cookies||[]).find(c=>c.name==='kc_compte');
 assert.ok(ck&&ck.httpOnly&&ck.sameSite==='Lax'&&!ck.secure&&ck.expires>Date.now()/1000+29*86400,'cookie de session : '+JSON.stringify(ck));
 await sleep(500);await capture('app_apres_creation_390');
 ok('création du premier administrateur, session ouverte, app affichée');
 // Réglages, Compte et sécurité.
 await js(`onglet('reglages');SET.cat='securite';await majComptes();renderSettings()`);await sleep(300);
 const texteReglages=await js(`document.getElementById('v-reglages').innerText`);
 assert.match(texteReglages,/Alex/);assert.match(texteReglages,/alex\.b/);assert.match(texteReglages,/Administrateur/);
 assert.match(texteReglages,/Se déconnecter/);assert.match(texteReglages,/Comptes utilisateurs/);assert.match(texteReglages,/Ajouter un utilisateur/);
 await debordement('réglages compte 390');await capture('reglages_compte_390');
 ok('Réglages : compte, déconnexion, comptes utilisateurs');
 // Session persistante : rechargement, splash court, app.
 const t0=Date.now();await ouvrir();await sleep(50);assert.equal(await js(couvre),true);
 await attendre(appVisible,6000);const duree=Date.now()-t0;
 assert.ok(duree<2600,'splash court avec session : '+duree+' ms');
 ok('session persistante, splash court ('+duree+' ms)');
 // Redémarrage du service : base persistante, session toujours valable.
 const dossierA=donnees;await arreterService();await demarrerService(dossierA);
 await ouvrir();await attendre(appVisible,6000);
 ok('redémarrage du service : compte et session conservés');
 // Deuxième appareil : sa propre session, sans toucher à la première.
 const {browserContextId}=await send('Target.createBrowserContext',{},null);
 const tel2=await onglet(browserContextId);await taille(375,667,tel2);
 // 2.6.104 : un appareil qui n'a jamais vu la présentation la voit avant la connexion.
 await ouvrir(tel2);await attendre(`${ecran}==='en-intro'`,6000,tel2);
 await js(`document.getElementById('en-passer').click()`,tel2);
 await attendre(`${ecran}==='en-connexion'`,6000,tel2);await sleep(600);
 await capture('connexion_375',tel2);await debordement('connexion 375',tel2);
 await remplir({'#in-l-id':'alex.b','#in-l-mdp':'mauvais-mdp'},tel2);
 await js(`document.querySelector('#f-connexion .en-cta').click()`,tel2);
 await attendre(`!document.getElementById('al-connexion').hidden`,6000,tel2);
 assert.match(await js(`document.getElementById('al-connexion').textContent`,tel2),/incorrect/);
 assert.equal(await js(`document.getElementById('in-l-mdp').value`,tel2),'','mot de passe vidé après un échec');
 await capture('connexion_erreur_375',tel2);
 ok('mauvais mot de passe refusé avec un message');
 await remplir({'#in-l-id':'ALEX.B','#in-l-mdp':'popcorn-2026'},tel2);
 await js(`document.querySelector('#f-connexion .en-cta').click()`,tel2);
 await attendre(appVisible,8000,tel2);
 await ouvrir();await attendre(appVisible,6000);
 ok('deux appareils connectés en même temps, chacun sa session');
 // Déconnexion du premier : le second reste connecté.
 await js(`seDeconnecter()`);await sleep(250);await js(`document.getElementById('m-oui').click()`);
 await attendre(`${ecran}==='en-connexion'&&!document.getElementById('entree').hidden`);
 await ouvrir(tel2);await attendre(appVisible,6000,tel2);
 await ouvrir();await attendre(`${ecran}==='en-connexion'`);
 ok('déconnexion de cet appareil seulement');
 // Route protégée sans session, routes techniques toujours joignables.
 const sans=await fetch(`http://127.0.0.1:${port}/reglages`);assert.equal(sans.status,401);
 assert.equal((await sans.json()).connexion,true);
 const siri=await fetch(`http://127.0.0.1:${port}/raccourci/faux/start`);assert.equal(siri.status,403,'raccourci Siri : son propre jeton, pas le cookie');
 const interne=await fetch(`http://127.0.0.1:${port}/interne/vu?type=movie&id=0`,{method:'POST'});assert.notEqual(interne.status,401,'route interne sans cookie');
 const pageLibre=await fetch(`http://127.0.0.1:${port}/`);assert.equal(pageLibre.status,200);
 const ins=await fetch(`http://127.0.0.1:${port}/auth/inscription`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nom:'X',identifiant:'intrus',mot_de_passe:'motdepasse'})});
 assert.equal(ins.status,403,'plus de premier compte possible, inscriptions fermées par défaut');assert.equal((await ins.json()).fermees,true);
 ok('route protégée, raccourci Siri et route interne indépendants du cookie');
 // Session expirée pendant l'utilisation : l'écran de connexion revient par dessus l'app.
 await remplir({'#in-l-id':'alex.b','#in-l-mdp':'popcorn-2026'});
 await js(`document.querySelector('#f-connexion .en-cta').click()`);await attendre(appVisible,8000);
 await send('Network.enable',{},session);await send('Network.clearBrowserCookies',{},session);
 await js(`api('/reglages')`);await attendre(`${ecran}==='en-connexion'&&!document.getElementById('entree').hidden`);
 ok('session perdue en cours d’usage : retour à la connexion');

 // ---------- Responsive ----------
 for(const [w,h] of [[320,568],[375,667],[390,844],[1280,800]]){
  await taille(w,h);await ouvrir();await attendre(`${ecran}==='en-connexion'`);await sleep(500);
  await debordement('connexion '+w);
  const cta=await js(`(()=>{const r=document.querySelector('#f-connexion .en-cta').getBoundingClientRect();return r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()`);
  assert.equal(cta,true,'bouton Se connecter visible à '+w);
  await capture('connexion_'+w);
 }
 // Clavier ouvert simulé : hauteur réduite, le formulaire défile jusqu'au bouton.
 await taille(375,360);await ouvrir();await attendre(`${ecran}==='en-connexion'`);await sleep(400);
 await js(`document.getElementById('in-l-mdp').focus();document.querySelector('#f-connexion .en-cta').scrollIntoView({block:'end'})`);await sleep(200);
 assert.equal(await js(`(()=>{const r=document.querySelector('#f-connexion .en-cta').getBoundingClientRect();return r.bottom<=innerHeight+1&&r.top>=0})()`),true,'bouton atteignable clavier ouvert');
 await debordement('clavier ouvert');await capture('connexion_clavier_375');
 ok('connexion responsive 320, 375, 390, bureau et clavier ouvert');

 // ---------- B. Installation existante avec un ancien code ----------
 await arreterService();
 const dossierB=mkdtempSync('/private/tmp/kc-donnees-ancien-');ancienCode(dossierB,'1234');
 writeFileSync(dossierB+'/reglages.json',JSON.stringify({icone:'clair',nom:'Alex'}));
 await demarrerService(dossierB);
 const sessB=await onglet();session=sessB;
 await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]},sessB);
 await taille(320,568);
 await send('Page.addScriptToEvaluateOnNewDocument',{source:"try{localStorage.setItem('kc_theme','light')}catch(e){}"},sessB);
 await ouvrir();await attendre(`${ecran}==='en-intro'`);await sleep(300);
 assert.equal(await js(`getComputedStyle(document.getElementById('entree')).backgroundColor`),'rgb(0, 0, 0)','entrée sombre même en thème clair');
 assert.equal(await js(`document.querySelector('meta[name=theme-color]').content`),'#000000');
 await capture('intro1_320_clair');await debordement('intro 320');
 for(let i=0;i<3;i++){await js(`document.getElementById('en-suivant').click()`);await sleep(120)}
 await capture('intro4_320');
 assert.equal(await js(`(()=>{const r=document.getElementById('en-suivant').getBoundingClientRect();return r.bottom<=innerHeight})()`),true,'Commencer visible à 320');
 await js(`document.getElementById('en-suivant').click()`);
 await attendre(`${ecran}==='en-ancien'`);await sleep(300);await capture('ancien_320');await debordement('ancien code 320');
 assert.equal(await js(`document.getElementById('in-ancien').maxLength`),4);
 await remplir({'#in-ancien':'0000'});
 await attendre(`document.getElementById('err-ancien').textContent!==''`);
 assert.match(await js(`document.getElementById('err-ancien').textContent`),/incorrect/);assert.equal(await js(ecran),'en-ancien');
 await capture('ancien_erreur_320');
 ok('ancien code incorrect refusé');
 // Sans ancien code confirmé, le service refuse la création.
 const force=await fetch(`http://127.0.0.1:${port}/auth/inscription`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nom:'X',identifiant:'intrus',mot_de_passe:'motdepasse'})});
 assert.equal(force.status,403);assert.equal((await force.json()).ancien_code,true);
 ok('création refusée sans l’ancien code');
 await remplir({'#in-ancien':'1234'});
 await attendre(`${ecran}==='en-compte'`);await sleep(200);await capture('compte_320');await debordement('compte 320');
 await remplir({'#in-c-nom':'Alex','#in-c-id':'alex','#in-c-mdp':'popcorn-2026','#in-c-mdp2':'popcorn-2026'});
 await js(`document.querySelector('#f-compte .en-cta').click()`);
 await attendre(`${ecran}==='en-bienvenue-admin'`,8000);await js(`document.getElementById('en-plus-tard').click()`);
 await attendre(appVisible,8000);
 assert.equal(await js(`document.documentElement.dataset.theme`),'light','thème clair de l’app retrouvé après l’entrée');
 await sleep(300);await capture('app_320_clair');
 assert.ok(existsSync(dossierB+'/auth.json'),'ancien code conservé pour un retour arrière');
 const eB=await etatService();assert.equal(eB.etat,'connexion');
 ok('migration : ancien code une dernière fois, puis administrateur, auth.json conservé');
 await ouvrir();await attendre(appVisible,6000);
 ok('après la migration, plus aucun code demandé');

 // ---------- C. État mixte : nouvelle interface, ancien service pas encore redémarré ----------
 const sessC=await onglet();session=sessC;await taille(390,844);
 await send('Fetch.enable',{patterns:[{urlPattern:'*/auth/etat*'}]},sessC);
 intercepter=(p,sid)=>send('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:401,responseHeaders:[{name:'Content-Type',value:'application/json'}],
  body:Buffer.from(JSON.stringify({ok:false,message:'Verrouillé',verrou:true})).toString('base64')},sid);
 await ouvrir();await attendre(`${ecran}==='en-erreur'`);
 assert.equal(await js(`document.getElementById('en-erreur-titre').textContent`),'Redémarrez le service');
 await sleep(500);await capture('ancien_service_390');
 intercepter=null;await send('Fetch.disable',{},sessC);
 await js(`document.getElementById('en-reessayer').click()`);await attendre(appVisible,8000);   // même navigateur que B, déjà connecté
 ok('ancien service non redémarré : message clair, puis Réessayer');

 assert.deepEqual(erreursJS,[],'Erreurs JavaScript');
 console.log('\n'+resultats.length+' vérifications réussies');
}catch(e){
 console.error('ÉCHEC',e);console.error(erreursJS.join('\n'));process.exitCode=1;
}finally{
 clearTimeout(minuterie);chrome.kill();await arreterService();
}
