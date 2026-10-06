// Entrée et configuration d'une installation (2.6.105) : installation neuve (présentation, premier compte Administrateur,
// bienvenue, « Configurons KamCiné »), états Configuration manquante côté Administrateur et Utilisateur, inscription publique
// fermée puis ouverte, relance depuis Réglages. Vrai service (uvicorn, Apple TV neutralisée, adresses externes fermées),
// données temporaires, Chrome sans écran. Il faut le Python du service :
// KAMCINE_PY=/chemin/vers/python node outils/test_configuration_ui.mjs   (captures dans /private/tmp/kc_config_*.png)
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {createServer as createHttpServer} from 'node:http';
import assert from 'node:assert/strict';

const racine=process.cwd(),PY=process.env.KAMCINE_PY||'python3';
const FERME='http://127.0.0.1:9';
// 2.6.107 : faux pyatv (outils/faux_pyatv.py, une Apple TV « Salon ») et faux pont Hue (serveur local ci dessous).
const LANCEUR=`
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(sys.argv[1]), "outils"))
import faux_pyatv
faux_pyatv.installer()
faux_pyatv.ETAT["appareils"] = json.loads(os.environ.get("FAUX_APPAREILS", "[]"))
sys.path.insert(0, sys.argv[1])
import main
main.atvlive.LIVE.demarrer = lambda: None
import uvicorn
uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
`;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const portLibre=()=>new Promise(r=>{const s=createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p))})});

let service=null,port=0;
// Faux pont Hue : bouton non pressé une fois, puis clé ; quatre lumières.
const PONT={port:0,demandes:0,cle:'fausse-cle-pont-ui',lumieres:[['l-1','Lampe gauche'],['l-2','Lampe droite'],['l-3','Enseigne'],['l-4','Plafonnier']]};
const DSM={port:0,utilisateur:'kamcine-test',motDePasse:'nas-secret-test'};
await new Promise(ok=>{const srv=createHttpServer((req,res)=>{let corps='';req.on('data',d=>corps+=d);req.on('end',()=>{
 const envoyer=o=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(o))};
 if(req.url==='/decouverte')return envoyer([{id:'pont1',internalipaddress:'127.0.0.1:'+PONT.port}]);
 if(req.method==='POST'&&req.url==='/api'){PONT.demandes++;return envoyer(PONT.demandes<2?[{error:{type:101,description:'link button not pressed'}}]:[{success:{username:PONT.cle}}])}
 if(req.headers['hue-application-key']!==PONT.cle){res.writeHead(403);return res.end('{}')}
 envoyer({data:PONT.lumieres.map(([id,name])=>({id,metadata:{name}}))});
})});srv.listen(0,'127.0.0.1',()=>{PONT.port=srv.address().port;PONT.srv=srv;ok()})});
await new Promise(ok=>{const srv=createHttpServer((req,res)=>{
 const u=new URL(req.url,'http://127.0.0.1'),p=Object.fromEntries(u.searchParams),send=o=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(o))};
 if(u.pathname==='/webapi/query.cgi')return send({success:true,data:{'SYNO.API.Auth':{path:'auth.cgi',maxVersion:6},'SYNO.Core.System':{path:'entry.cgi',maxVersion:3},'SYNO.Core.Storage.Volume':{path:'entry.cgi',maxVersion:1}}});
 if(u.pathname==='/webapi/auth.cgi')return p.method==='login'?(p.account===DSM.utilisateur&&p.passwd===DSM.motDePasse?send({success:true,data:{sid:'private-test-sid'}}):send({success:false,error:{code:400}})):send({success:true,data:{}});
 if(u.pathname==='/webapi/entry.cgi'&&p.api==='SYNO.Core.System')return send({success:true,data:{model:'DS Test',uptime:172800,version_string:'DSM 7.test'}});
 if(u.pathname==='/webapi/entry.cgi'&&p.api==='SYNO.Core.Storage.Volume')return send({success:true,data:{volumes:[{display_name:'Volume 1',size_total:8000000000000,size_used:5200000000000},{display_name:'Volume 2',size_total:4000000000000,size_used:2200000000000}]}});
 send({success:false,error:{code:404}});
});srv.listen(0,'127.0.0.1',()=>{DSM.port=srv.address().port;DSM.srv=srv;ok()})});
async function demarrerService(dossier){
 port=await portLibre();
 const env={...process.env,KAMCINE_APP:racine,KAMCINE_DATA:dossier,KAMCINE_TIMEZONE:'Asia/Kathmandu',TMDB_BASE:FERME,OMDB_BASE:FERME,TRAKT_BASE:FERME,INTRODB_BASE:FERME,
  YOUTUBE_RSS:FERME,OEMBED_BASE:FERME,YOUTUBE_API_BASE:FERME,OVERSEERR_LOCAL:FERME,PYTHONWARNINGS:'ignore',SYNOLOGY_URL:'',SYNOLOGY_USER:'',SYNOLOGY_PASSWORD:'',
  HUE_SCHEME:'http',HUE_DISCOVERY_URL:`http://127.0.0.1:${PONT.port}/decouverte`,FAUX_APPAREILS:JSON.stringify([['AA:BB:CC:DD:EE:01','Salon'],['AA:BB:CC:DD:EE:02','Chambre']]),COMP:'',AIR:''};
 delete env.KAMCINE_DIR;
 service=spawn(PY,['-c',LANCEUR,racine+'/app',String(port)],{env,cwd:mkdtempSync('/private/tmp/kc-cwd-'),stdio:['ignore','pipe','pipe'],detached:true});
 let sortie='';service.stdout.on('data',d=>sortie+=d);service.stderr.on('data',d=>sortie+=d);
 for(let i=0;i<100;i++){try{const r=await fetch(`http://127.0.0.1:${port}/auth/etat`);if(r.ok)return}catch(e){}
  if(service.exitCode!==null)throw new Error('Service arrêté : '+sortie);await sleep(150)}
 throw new Error('Service injoignable : '+sortie);
}

const chrome=spawn(process.env.KAMCINE_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-pipe','--user-data-dir='+mkdtempSync('/private/tmp/kc-config-chrome-'),'about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe'],detached:true});
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
async function capture(sid,nom){const c=await send('Page.captureScreenshot',{format:'png'},sid);writeFileSync(`/private/tmp/kc_config_${nom}.png`,Buffer.from(c.data,'base64'))}
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


async function entreeA(sid){await ouvrir(sid);await attendre(sid,`typeof ENTREE!=='undefined'&&!!ENTREE.ecran`,10000)}
async function etape(sid){return js(sid,`CFG.ouvert?CFG_ETAPES[CFG.etape].id:null`)}
async function pageVisible(sid,nom){await sleep(350);await debordement(sid,nom);await capture(sid,nom)}
const minuterie=setTimeout(()=>{console.error('Expiration du test');chrome.kill();service&&service.kill();process.exit(1)},300000);
const resultats=[];const ok=t=>{resultats.push(t);console.log('ok ',t)};
try{
 const donnees=mkdtempSync('/private/tmp/kc-donnees-config-');
 await demarrerService(donnees);

 // ---------- A, G. Installation neuve : présentation, premier compte, bienvenue, configuration ----------
 const admin=await telephone();
 await entreeA(admin);
 await attendre(admin,`ENTREE.ecran==='en-intro'`);
 await clic(admin,'#en-passer');
 await attendre(admin,`ENTREE.ecran==='en-compte'`);
 assert.match(await texte(admin,'#en-compte'),/Créez votre compte/);
 await remplir(admin,{'#in-c-nom':'Alice Test','#in-c-id':'alice','#in-c-mdp':'popcorn-2026','#in-c-mdp2':'popcorn-2026'});
 await clic(admin,'#f-compte .en-cta');
 await attendre(admin,`ENTREE.ecran==='en-bienvenue-admin'`,10000);
 assert.equal(await texte(admin,'#en-bienvenue-admin-titre'),'Bienvenue Alice 👋');
 assert.match(await texte(admin,'#en-bienvenue-admin'),/Votre compte est prêt\. Configurons maintenant votre installation KamCiné\.[\s\S]*Configurer KamCiné[\s\S]*Plus tard/);
 assert.equal(await js(admin,`AUTH.compte.admin`),true,'premier compte Administrateur');
 for(const [w,h] of [[320,568],[375,667],[390,844]]){await taille(admin,w,h);await sleep(450);await debordement(admin,'bienvenue admin '+w);await capture(admin,'bienvenue_admin_'+w)}
 await taille(admin,390,844);
 await clic(admin,'#en-configurer');
 await attendre(admin,`CFG.ouvert&&!document.getElementById('config').hidden&&${appPrete}`,10000);
 await attendre(admin,`!!INST.services`);
 ok('A, G. Installation neuve : présentation, création du premier compte (Administrateur), bienvenue, Configurer KamCiné');

 if(process.env.KAMCINE_NAS_ONLY==='1'){
  await clic(admin,'#cfg-tard');await attendre(admin,`!CFG.ouvert&&VUE==='seance'`);
  await js(admin,`onglet('appareils')`);await attendre(admin,`!!document.querySelector('#appareils-body [data-t="syno-config"]')`,10000);
  assert.match(await texte(admin,'#appareils-body'),/À configurer/);
  await clic(admin,'#appareils-body [data-t="syno-config"]');await attendre(admin,`!!document.getElementById('syno-url')`);
  await remplir(admin,{'#syno-nom':'NAS de test','#syno-url':`http://127.0.0.1:${DSM.port}`,'#syno-user':DSM.utilisateur,'#syno-password':DSM.motDePasse});
  await clic(admin,'#modal [data-t="syno-test-form"]');await attendre(admin,`document.getElementById('syno-form-result')?.textContent.includes('réussie')`,10000);
  assert.doesNotMatch(await texte(admin,'#modal'),/nas-secret-test/);
  await clic(admin,'#modal [data-t="syno-save"]');await attendre(admin,`DEV.synology?.en_ligne===true&&DEV.synology?.nom==='NAS de test'`,10000);
  const confPublique=await js(admin,`fetch('/appareils/synology/configuration').then(r=>r.json())`,true);
  assert.equal(confPublique.mot_de_passe_configure,true);assert.equal(Object.hasOwn(confPublique,'mot_de_passe'),false);assert.doesNotMatch(JSON.stringify(confPublique),/nas-secret-test/);
  const resumeNAS=await texte(admin,'#appareils-body .nas-card');assert.ok(resumeNAS,'Carte NAS absente après enregistrement');
  assert.match(resumeNAS,/DS Test/);assert.match(resumeNAS,/7,4 To \/ 12,0 To/);
  await clic(admin,'#appareils-body [data-t="syno-details"]');await attendre(admin,`document.getElementById('modal').classList.contains('show')&&document.querySelector('.nas-details')`);
  assert.match(await texte(admin,'.nas-details'),/DSM 7\.test[\s\S]*Volume 1[\s\S]*Volume 2/);
  for(const w of [320,360,390,1280]){await taille(admin,w,844);await debordement(admin,'NAS_details_'+w)}
  await clic(admin,'#m-x');await sleep(250);
  const notes=await js(admin,`(()=>{const host=document.createElement('div');host.innerHTML='<div class="pg-tt"><h1>Un titre de test (2025)</h1><div class="pg-meta"><span>Film</span><span>Action</span></div><div class="pg-notes"><button class="note pg-note-perso"><span class="star">★</span><span>Noter</span></button></div></div>';document.body.append(host);const h=host.querySelector('h1'),m=host.querySelector('.pg-meta'),n=host.querySelector('.pg-note-perso>span:not(.star)');const r={titleMargin:getComputedStyle(h).marginBottom,metaMargin:getComputedStyle(m).marginBottom,noteColor:getComputedStyle(n).color,textColor:getComputedStyle(document.body).color};host.remove();return r})()`);
  assert.equal(notes.noteColor,notes.textColor,'Noter doit avoir la couleur blanche du texte principal');assert.equal(notes.titleMargin,'2px');assert.equal(notes.metaMargin,'2px');
  assert.deepEqual(erreursJS,[],'Erreurs JavaScript : '+erreursJS.join('\n'));
  ok('NAS et finition fiche : test/sauvegarde DSM, aucun secret retourné, volumes détaillés, 320/360/390/desktop sans overflow, Noter blanc et espacements');
 }else{

 // ---------- Étapes de l'assistant, états honnêtes, largeurs ----------
 assert.equal(await etape(admin),'appletv');
 assert.match(await texte(admin,'#cfg-corps'),/KamCiné utilise votre Apple TV et Infuse pour lancer et piloter vos séances\.[\s\S]*Non configurée/);
 assert.equal(await texte(admin,'#cfg-etape'),'Étape 1 sur 5');
 assert.equal(await js(admin,`document.getElementById('cfg-retour').hidden`),true);
 for(const [w,h,n] of [[320,568,'320'],[375,667,'375'],[390,844,'390'],[1280,800,'bureau']]){await taille(admin,w,h);await pageVisible(admin,'config_appletv_'+n)}
 await taille(admin,390,844);
 await clic(admin,'#cfg-suivant');
 // 2.6.108 : TMDB a son étape ; Radarr, Sonarr et Overseerr la leur.
 assert.equal(await etape(admin),'tmdb');
 assert.match(await texte(admin,'#cfg-corps'),/Votre catalogue[\s\S]*TMDB[\s\S]*requis pour le catalogue[\s\S]*Obtenir une clé TMDB/);
 assert.equal(await js(admin,`document.querySelector('#cfg-corps a.svc-lien').href`),'https://www.themoviedb.org/settings/api');
 const champCle=await js(admin,`(()=>{const i=document.getElementById('tmdb-cle'),c=getComputedStyle(i);return [i.type,i.autocomplete,i.name,c.webkitTextSecurity||'none']})()`);
 assert.deepEqual(champCle,['text','off','kamcine-cle-service','none'],'champ de clé jamais présenté comme un mot de passe');
 await pageVisible(admin,'config_tmdb_390');
 await clic(admin,'#cfg-suivant');
 assert.equal(await etape(admin),'media');
 const media=await texte(admin,'#cfg-corps');
 assert.match(media,/Radarr[\s\S]*Gestion de vos films[\s\S]*Sonarr[\s\S]*Gestion de vos séries[\s\S]*Overseerr[\s\S]*facultatif/);
 assert.doesNotMatch(media,/TMDB/);
 assert.equal(await js(admin,`['arr-url-radarr_hd','arr-url-radarr_uhd','arr-url-sonarr_hd','arr-url-sonarr_uhd','ov-cle'].every(i=>document.querySelectorAll('#'+i).length===1)`),true,'HD et 4K, mêmes champs que Réglages, identifiants uniques');
 assert.equal(await js(admin,`['arr-url-radarr_hd','arr-url-sonarr_uhd','ov-url'].map(i=>document.getElementById(i).value).join('')`),'','aucune adresse supposée');
 assert.equal(await js(admin,`[...document.querySelectorAll('#cfg-corps input')].some(i=>/127\.0\.0\.1/.test(i.value+i.placeholder))`),false,'jamais 127.0.0.1 présenté comme la bonne adresse');
 assert.match(media,/Aucune adresse enregistrée : le service utilise http:\/\/127\.0\.0\.1:\d+/,'Overseerr : l’adresse réellement utilisée est dite');
 assert.equal(await js(admin,`document.querySelectorAll('#cfg-corps [data-t="svc-ouvrir"]').length`),5,'Ouvrir Radarr HD, 4K, Sonarr HD, 4K, Overseerr');
 const ouvert=await js(admin,`(()=>{let u='';const o=window.open;window.open=x=>{u=x};document.getElementById('arr-url-radarr_hd').value='http://127.0.0.1:7878';document.querySelector('[data-champ="arr-url-radarr_hd"]').click();window.open=o;return u})()`);
 assert.equal(ouvert,'http://127.0.0.1:7878/settings/general','clé d’API de Radarr : Réglages, Général');
 await js(admin,`document.getElementById('arr-url-radarr_hd').value=''`);
 await pageVisible(admin,'config_media_390');
 await taille(admin,320,568);await pageVisible(admin,'config_media_320');await taille(admin,390,844);
 await clic(admin,'#cfg-suivant');
 assert.equal(await etape(admin),'telechargements');
 assert.match(await texte(admin,'#cfg-corps'),/Connectez Transmission pour suivre vos téléchargements directement dans KamCiné\./);
 assert.equal(await js(admin,`!!document.getElementById('tr-url')`),true);
 await pageVisible(admin,'config_telechargements_390');
 await clic(admin,'#cfg-suivant');
 assert.equal(await etape(admin),'cinema');
 assert.match(await texte(admin,'#cfg-corps'),/Enrichissez vos séances[\s\S]*Philips Hue[\s\S]*Denon/);
 // P. Modifier dans l'assistant = modifier les Réglages (même réglage, même route).
 await js(admin,`(()=>{const i=document.getElementById('cfg-amp-ip');i.value='192.168.1.77';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 await attendre(admin,`REG.denon_ip==='192.168.1.77'`);
 // 2.6.106 : installation vierge, ampli non piloté tant que l'administrateur ne l'active pas.
 assert.equal(await js(admin,`document.querySelector('#cfg-corps [data-t="sw"][data-k="denon_actif"]').getAttribute('aria-checked')`),'false');
 await clic(admin,'#cfg-corps [data-t="sw"][data-k="denon_actif"]');
 await attendre(admin,`REG.denon_actif===true&&INST.services&&INST.services.denon.configuree===true`);
 assert.equal(await js(admin,`fetch('/reglages').then(r=>r.json()).then(j=>j.denon_ip)`),'192.168.1.77');
 await pageVisible(admin,'config_cinema_390');
 ok('Étapes Apple TV, services média (HD et 4K), téléchargements, cinéma ; P. adresse de l’ampli saisie dans l’assistant = réglage enregistré');
 // 2.6.107 : Philips Hue depuis l'assistant : recherche, bouton du pont, clé, rôles des lumières.
 assert.match(await texte(admin,'#cfg-corps'),/Philips Hue\s*Facultatif/);
 await clic(admin,'#cfg-corps [data-t="hue-rechercher"]');
 await attendre(admin,`!!document.querySelector('#cfg-corps [data-t="hue-associer"]')`,15000);
 await clic(admin,'#cfg-corps [data-t="hue-associer"]');
 await attendre(admin,`APP.hue.mode==='bouton'`);
 assert.match(await texte(admin,'#cfg-corps'),/Appuyez sur le bouton rond au centre du pont Hue/);
 await pageVisible(admin,'hue_bouton_390');
 await attendre(admin,`APP.hue.mode==='lumieres'&&document.querySelectorAll('#cfg-corps select[data-lumiere]').length===4`,15000);
 await js(admin,`(()=>{const r={'l-1':'ecran','l-2':'ecran','l-3':'panneau','l-4':'salle'};document.querySelectorAll('#cfg-corps select[data-lumiere]').forEach(s=>s.value=r[s.dataset.lumiere])})()`);
 await pageVisible(admin,'hue_lumieres_390');
 await taille(admin,320,568);await pageVisible(admin,'hue_lumieres_320');await taille(admin,390,844);
 await clic(admin,'#cfg-corps [data-t="hue-roles"]');
 await toastVu(admin,'Rôles des lumières enregistrés');
 await attendre(admin,`APP.etat&&APP.etat.hue.roles_associes===true`);
 assert.match(await texte(admin,'#cfg-corps'),/Philips Hue\s*✓ Configuré/);
 assert.equal(await js(admin,`JSON.stringify(APP).includes('${'fausse-cle-pont-ui'}')||document.body.innerHTML.includes('fausse-cle-pont-ui')`),false,'la clé du pont ne vient jamais au navigateur');
 ok('Philips Hue depuis l’assistant : pont trouvé, bouton pressé, rôles des lumières enregistrés, clé jamais dans le navigateur');
 await clic(admin,'#cfg-suivant');
 assert.equal(await etape(admin),'fin');
 const fin=await texte(admin,'#cfg-corps');
 assert.match(fin,/KamCiné est prêt 🍿/);
 assert.match(fin,/Apple TV\s*Requis[\s\S]*TMDB\s*Requis[\s\S]*Radarr\s*Facultatif[\s\S]*Philips Hue\s*Configuré[\s\S]*Denon\s*Configuré/);
 assert.equal(await texte(admin,'#cfg-suivant'),'Découvrir KamCiné');
 await pageVisible(admin,'config_fin_390');
 await taille(admin,1280,800);await pageVisible(admin,'config_fin_bureau');await taille(admin,390,844);
 await clic(admin,'#cfg-retour');assert.equal(await etape(admin),'cinema','Retour');
 await clic(admin,'#cfg-suivant');await clic(admin,'#cfg-suivant');
 await attendre(admin,`!CFG.ouvert&&VUE==='seance'`);
 ok('Fin : « KamCiné est prêt 🍿 », résumé réel (Requis, Facultatif, Configuré), Retour, Découvrir KamCiné');

 // ---------- J, K, L, M. Configuration manquante, côté Administrateur ----------
 await attendre(admin,`document.documentElement.classList.contains('cfg-sans-seances')`);
 assert.match(await texte(admin,'#cfg-seance'),/Apple TV n’est pas encore configurée[\s\S]*Configurez votre Apple TV pour profiter des séances KamCiné\.[\s\S]*Terminer la configuration/);
 assert.equal(await js(admin,`getComputedStyle(document.getElementById('sc-bloc')).display`),'none');
 await pageVisible(admin,'manque_accueil_admin');
 await js(admin,`onglet('catalogue')`);await attendre(admin,`!!document.querySelector('#catalogue-body .cfg-manque')`);
 assert.match(await texte(admin,'#catalogue-body'),/Votre catalogue n’est pas encore prêt\.[\s\S]*Connectez vos services média pour commencer\.[\s\S]*Terminer la configuration/);
 assert.doesNotMatch(await texte(admin,'#catalogue-body'),/NAS/);
 await pageVisible(admin,'manque_catalogue_admin');
 await clic(admin,'#catalogue-body [data-t="config-ouvrir"]');
 await attendre(admin,`CFG.ouvert`);assert.equal(await etape(admin),'tmdb','le bouton mène à l’étape des services média');
 await clic(admin,'#cfg-tard');await attendre(admin,`!CFG.ouvert`);
 await js(admin,`onglet('telechargements')`);await attendre(admin,`!!document.querySelector('#dl-content .cfg-manque')`,10000);
 assert.match(await texte(admin,'#dl-content'),/Connectez Transmission[\s\S]*Configurer Transmission/);
 await pageVisible(admin,'manque_telechargements_admin');
 await js(admin,`onglet('appareils')`);await sleep(200);
 assert.doesNotMatch(await texte(admin,'#appareils-body'),/Aucun appareil n’est encore configuré/,'Denon configuré : pas d’état vide');
 ok('J, K, L, M. Séances, catalogue, téléchargements manquants : cartes propres avec bouton de configuration pour l’administrateur');

 // ---------- O, Q, R. Relance depuis Réglages ; Réglages = assistant ; retrait = état manquant ----------
 await js(admin,`onglet('reglages');SET.cat=null;renderSettings()`);
 assert.equal(await js(admin,`document.querySelector('.cat-line').dataset.t`),'config-ouvrir');
 assert.match(await texte(admin,'.cat-line'),/Configuration de KamCiné/);
 await capture(admin,'reglages_admin');
 await js(admin,`save({denon_actif:false})`);                                          // Q, R : depuis Réglages
 await clic(admin,'.cat-line[data-t="config-ouvrir"]');
 await attendre(admin,`CFG.ouvert&&INST.services&&INST.services.denon.configuree===false`);
 await js(admin,`allerEtapeConfig(5)`);
 assert.match(await texte(admin,'#cfg-corps'),/Denon\s*Facultatif/,'retirer la configuration dans Réglages se voit dans l’assistant');
 await js(admin,`allerEtapeConfig(4)`);
 assert.equal(await js(admin,`document.querySelector('#cfg-corps [data-t="sw"][data-k="denon_actif"]').getAttribute('aria-checked')`),'false');
 await clic(admin,'#cfg-corps [data-t="sw"][data-k="denon_actif"]');await attendre(admin,`REG.denon_actif===true&&INST.services.denon.configuree===true`);
 await clic(admin,'#cfg-tard');await attendre(admin,`!CFG.ouvert`);
 await js(admin,`onglet('appareils')`);await sleep(150);
 assert.equal(await js(admin,`document.getElementById('amp-ip').value`),'192.168.1.77','même adresse dans Appareils');
 ok('O, Q, R. Configuration relançable depuis Réglages ; Réglages et assistant lisent les mêmes réglages ; un retrait redevient « Facultatif »');

 // ---------- C, D. Installation existante : Login avec S'inscrire ; inscriptions fermées ----------
 const paul=await telephone();
 await entreeA(paul);await attendre(paul,`ENTREE.ecran==='en-intro'`);await clic(paul,'#en-passer');
 await attendre(paul,`ENTREE.ecran==='en-connexion'`);
 assert.match(await texte(paul,'#en-connexion'),/Pas encore de compte \? S’inscrire/);
 await clic(paul,'#en-connexion [data-entree="en-inscription"]');
 await attendre(paul,`ENTREE.ecran==='en-inscription'&&!document.getElementById('en-inscription-fermee').hidden`);
 assert.match(await texte(paul,'#en-inscription-fermee'),/Les inscriptions sont fermées[\s\S]*Demandez à un administrateur KamCiné de vous créer un compte\./);
 assert.equal(await js(paul,`document.getElementById('f-inscription').hidden`),true);
 for(const w of [320,390]){await taille(paul,w,w===320?568:844);await pageVisible(paul,'inscriptions_fermees_'+w)}
 await clic(paul,'#en-inscription-fermee [data-entree="en-connexion"]');await attendre(paul,`ENTREE.ecran==='en-connexion'`);
 ok('C, D. Login avec S’inscrire ; inscriptions fermées : page claire, sans erreur technique');

 // ---------- E, F, I. Inscriptions ouvertes : compte Utilisateur, bienvenue, aucune configuration ----------
 await js(admin,`onglet('reglages');SET.cat='securite';renderSettings()`);await attendre(admin,`!!document.querySelector('[data-t="cpt-inscriptions"]')`);
 assert.match(await texte(admin,'#reglages-body'),/Autoriser les inscriptions[\s\S]*Permet aux personnes ayant accès à cette installation KamCiné de créer leur propre compte\./);
 assert.equal(await js(admin,`document.querySelector('[data-t="cpt-inscriptions"]').getAttribute('aria-checked')`),'false','désactivé par défaut');
 await clic(admin,'[data-t="cpt-inscriptions"]');await toastVu(admin,'Inscriptions ouvertes');
 await clic(paul,'#en-connexion [data-entree="en-inscription"]');
 await attendre(paul,`ENTREE.ecran==='en-inscription'&&!document.getElementById('f-inscription').hidden`);
 assert.equal(await js(paul,`getComputedStyle(document.querySelector('#f-inscription .en-cta')).backgroundColor`),'rgb(52, 195, 143)');
 assert.equal(await js(paul,`['in-i-nom','in-i-id','in-i-mdp','in-i-mdp2'].map(i=>document.getElementById(i).autocomplete).join()`),'given-name,username,new-password,new-password');
 await clic(paul,'#f-inscription .en-cta');
 assert.equal(await texte(paul,'#err-i-nom'),'Indiquez votre prénom.');
 await remplir(paul,{'#in-i-nom':'Paul','#in-i-id':'alice','#in-i-mdp':'cinema-paul','#in-i-mdp2':'cinema-paul'});
 await clic(paul,'#f-inscription .en-cta');await attendre(paul,`document.getElementById('err-i-id').textContent.includes('déjà pris')`);
 for(const w of [320,375,390]){await taille(paul,w,w===320?568:844);await pageVisible(paul,'inscription_'+w)}
 await taille(paul,390,844);
 await remplir(paul,{'#in-i-id':'paul'});
 await clic(paul,'#f-inscription .en-cta');
 await attendre(paul,`ENTREE.ecran==='en-bienvenue'`,10000);
 assert.equal(await texte(paul,'#en-bienvenue-titre'),'Bienvenue Paul 👋');
 assert.equal(await js(paul,`AUTH.compte.role`),'utilisateur','inscription publique : toujours Utilisateur');
 await clic(paul,'#en-decouvrir');await attendre(paul,appPrete,10000);
 assert.equal(await js(paul,`CFG.ouvert`),false,'aucune configuration technique pour un Utilisateur');
 ok('E, F, I. Inscription ouverte : compte Utilisateur, « Bienvenue Paul 👋 », aucun écran de configuration');

 // ---------- N. Côté Utilisateur : message pour l'administrateur, aucun bouton de configuration ----------
 await attendre(paul,`document.documentElement.classList.contains('cfg-sans-seances')`);
 assert.match(await texte(paul,'#cfg-seance'),/Les séances ne sont pas encore disponibles\.[\s\S]*Un administrateur doit terminer la configuration de KamCiné\./);
 assert.equal(await js(paul,`document.querySelectorAll('[data-t="config-ouvrir"]').length`),0);
 assert.equal(await js(paul,`getComputedStyle(document.getElementById('sc-vide')).display+getComputedStyle(document.getElementById('sc-bloc')).display`),'nonenone','aucun bloc de séance sous la carte');
 await pageVisible(paul,'manque_accueil_utilisateur');
 await js(paul,`onglet('catalogue')`);await attendre(paul,`!!document.querySelector('#catalogue-body .cfg-manque')`);
 assert.match(await texte(paul,'#catalogue-body'),/Le catalogue n’est pas encore disponible\.[\s\S]*Un administrateur doit terminer la configuration\./);
 assert.equal(await js(paul,`document.querySelectorAll('#catalogue-body button').length`),0);
 await pageVisible(paul,'manque_catalogue_utilisateur');
 await js(paul,`onglet('telechargements')`);await attendre(paul,`!!document.querySelector('#dl-content .cfg-manque')`,10000);
 assert.match(await texte(paul,'#dl-content'),/Les téléchargements ne sont pas encore configurés\./);
 await js(paul,`ouvrirConfiguration(0)`);assert.equal(await js(paul,`CFG.ouvert`),false);
 assert.equal(await js(paul,`fetch('/installation/etat').then(r=>r.json()).then(j=>'services' in j)`),false);
 await js(paul,`onglet('reglages');SET.cat=null;renderSettings()`);
 assert.equal(await js(paul,`!!document.querySelector('[data-t="config-ouvrir"]')`),false,'pas d’entrée Configuration de KamCiné');
 ok('N. Utilisateur : messages qui renvoient vers un administrateur, aucun bouton ni accès à la configuration');

 // ---------- Y. Thème : l'assistant reste sombre, zones sûres prises en compte ----------
 await js(admin,`document.documentElement.setAttribute('data-theme','light');ouvrirConfiguration(0)`);
 await attendre(admin,`CFG.ouvert`);
 assert.equal(await js(admin,`getComputedStyle(document.getElementById('config')).backgroundColor`),'rgb(0, 0, 0)');
 assert.match(await js(admin,`[...document.styleSheets[0].cssRules].filter(r=>r.selectorText&&/cfg-(haut|bas)/.test(r.selectorText)).map(r=>r.cssText).join(' ')`),/safe-area-inset-top[\s\S]*safe-area-inset-bottom/);
 await capture(admin,'config_theme_clair');
 await clic(admin,'#cfg-tard');await js(admin,`document.documentElement.setAttribute('data-theme','dark')`);
 ok('Y. Assistant toujours sombre, même en thème clair ; zones sûres haut et bas');

 // ---------- 2.6.107 : Apple TV depuis la page Appareils : découverte, deux codes, mauvais code, état final ----------
 await js(admin,`onglet('appareils')`);await attendre(admin,`!!document.querySelector('#appareils-body [data-t="atv-rechercher"]')`,10000);
 assert.match(await texte(admin,'#appareils-body'),/Apple TV/);
 assert.equal(await js(admin,`document.querySelectorAll('#appareils-body [data-t="atv-power"]').length`),0,'Pas de commandes Apple TV tant qu’aucun appareil n’est configuré');
 assert.equal(await js(admin,"[...document.querySelectorAll('#appareils-body details.dev-advanced')].some(d=>d.open)"),false,'Les réglages avancés sont repliés par défaut');
 await clic(admin,'#appareils-body [data-t="atv-rechercher"]');
 await attendre(admin,`!!document.querySelector('#appareils-body [data-t="atv-appairer"]')`,40000);
 assert.match(await texte(admin,'#appareils-body .dcfg'),/Salon/);
 await pageVisible(admin,'atv_liste_390');
 await clic(admin,'#appareils-body [data-t="atv-appairer"]');
 await attendre(admin,`APP.atv.mode==='pin'&&!!document.querySelector('#appareils-body input[name=pin]')`,30000);
 assert.match(await texte(admin,'#appareils-body .dcfg'),/Étape 1 sur 2 · Salon[\s\S]*Un second code suivra/);
 await remplir(admin,{'#appareils-body input[name=pin]':'0000'});await clic(admin,'#appareils-body [data-t="atv-pin"]');
 await attendre(admin,`APP.atv.mode==='recherche'&&APP.atv.erreur.includes('Code refusé')`,30000);
 await pageVisible(admin,'atv_code_refuse_390');
 await clic(admin,'#appareils-body [data-t="atv-appairer"]');
 await attendre(admin,`APP.atv.mode==='pin'&&APP.atv.numero===1`,30000);
 await remplir(admin,{'#appareils-body input[name=pin]':'1234'});await clic(admin,'#appareils-body [data-t="atv-pin"]');
 await attendre(admin,`APP.atv.mode==='pin'&&APP.atv.numero===2`,30000);
 // 2.6.108 : le premier code accepté est dit, en vert ; l'étape 2 est annoncée ; rien en rouge.
 const deux=await texte(admin,'#appareils-body .dcfg');
 assert.match(deux,/Étape 2 sur 2[\s\S]*✓ Premier code accepté\.[\s\S]*Saisissez maintenant le nouveau code affiché sur l’Apple TV pour autoriser la lecture et son suivi/);
 assert.equal(await js(admin,`!!document.querySelector('#appareils-body .dcfg-msg.bad')`),false);
 assert.equal(await js(admin,`document.querySelector('#appareils-body input[name=pin]').value`),'');
 assert.doesNotMatch(await js(admin,`(()=>{const i=document.querySelector('#appareils-body input[name=pin]');i.focus();return getComputedStyle(i).boxShadow})()`),/245, 9, 31/,'pas de rouge sur le champ du second code');
 await pageVisible(admin,'atv_pin_390');
 await taille(admin,320,568);await pageVisible(admin,'atv_pin_320');await taille(admin,390,844);
 await remplir(admin,{'#appareils-body input[name=pin]':'5678'});await clic(admin,'#appareils-body [data-t="atv-pin"]');
 await toastVu(admin,'Apple TV appairée');
 await attendre(admin,`APP.etat.appletv.configuree===true&&!!document.querySelector('#appareils-body [data-t="atv-tester"]')`,15000);
 assert.match(await texte(admin,'#appareils-body .dcfg'),/✓ Configurée\s*Salon/);
 assert.equal(await js(admin,`document.querySelectorAll('#appareils-body [data-t="atv-power"]').length`),2,'Réveil et veille proposés sur la carte de l’appareil configuré');
 await clic(admin,'#appareils-body [data-t="atv-tester"]');await toastVu(admin,'Connexion réussie');
 await pageVisible(admin,'appareils_configures_390');
 assert.equal(await js(admin,`document.body.innerHTML.includes('faux-identifiant')||JSON.stringify(APP).includes('faux-identifiant')`),false,'identifiants d’appairage jamais dans le navigateur');
 await attendre(admin,`INST.pret&&INST.pret.seances===true`,10000);
 await js(admin,`onglet('seance')`);
 assert.equal(await js(admin,`document.documentElement.classList.contains('cfg-sans-seances')`),false,'la carte de configuration manquante disparaît');
 ok('Apple TV depuis Appareils : découverte, code refusé, deux codes (premier accepté annoncé), ✓ Configurée, test, identifiants jamais dans le navigateur');

 // ---------- 2.6.108 : une seconde Apple TV, nom KamCiné, choix pour la séance ----------
 await js(admin,`onglet('appareils')`);await attendre(admin,`!!document.querySelector('#appareils-body [data-t="atv-changer"]')`);
 await clic(admin,'#appareils-body [data-t="atv-changer"]');await clic(admin,'#appareils-body [data-t="atv-rechercher"]');
 await attendre(admin,`document.querySelectorAll('#appareils-body [data-t="atv-appairer"]').length===2`,40000);
 assert.match(await texte(admin,'#appareils-body .dcfg'),/Salon\s*Déjà appairée[\s\S]*Chambre/);
 await clic(admin,'#appareils-body [data-t="atv-appairer"][data-v="AA:BB:CC:DD:EE:02"]');
 await attendre(admin,`APP.atv.mode==='pin'&&APP.atv.numero===1`,30000);
 await remplir(admin,{'#appareils-body input[name=pin]':'1234'});await clic(admin,'#appareils-body [data-t="atv-pin"]');
 await attendre(admin,`APP.atv.numero===2`,30000);
 await remplir(admin,{'#appareils-body input[name=pin]':'5678'});await clic(admin,'#appareils-body [data-t="atv-pin"]');
 await toastVu(admin,'Apple TV appairée');
 await attendre(admin,`((APP.etat.appletv||{}).appareils||[]).length===2`,15000);
 assert.match(await texte(admin,'#appareils-body .dcfg'),/Salon[\s\S]*Apple TV utilisée pour les séances[\s\S]*Chambre/,'l’Apple TV déjà utilisée le reste');
 await clic(admin,'#appareils-body [data-t="atv-renommer"][data-v="AA:BB:CC:DD:EE:02"]');
 await remplir(admin,{'#appareils-body input[name=atv-nom]':'Chambre des parents'});
 await clic(admin,'#appareils-body [data-t="atv-nom-ok"]');await toastVu(admin,'Nom enregistré');
 await attendre(admin,`APP.etat.appletv.appareils.some(a=>a.nom==='Chambre des parents'&&a.nom_detecte==='Chambre')`);
 await pageVisible(admin,'atv_deux_390');
 await taille(admin,320,568);await pageVisible(admin,'atv_deux_320');await taille(admin,390,844);
 await attendre(admin,`INST.pret.appletvs.length===2`,10000);
 const appUi=await js(admin,"JSON.stringify({titres:[...document.querySelectorAll('#appareils-body .sec-title')].map(e=>e.textContent.trim()),details:[...document.querySelectorAll('#appareils-body details.dev-advanced')].map(e=>e.open),nom:document.querySelector('#appareils-body .group .row .rt')?.textContent})").then(JSON.parse);
 assert.ok(appUi.titres.includes('Apple TV'),'Titre principal Apple TV seul et clair');assert.ok(appUi.nom&&!/^Apple TV/.test(appUi.nom),'Nom de l’appareil séparé du type Apple TV');assert.ok(appUi.details.length>=2&&appUi.details.every(x=>!x),'Les réglages avancés restent repliés par défaut');
 await clic(admin,'#appareils-body details.dev-advanced summary');assert.equal(await js(admin,"document.querySelector('#appareils-body details.dev-advanced').open"),true);assert.match(await texte(admin,'#appareils-body details.dev-advanced:first-of-type summary'),/Masquer les réglages avancés/);
 await clic(admin,'#appareils-body details.dev-advanced summary');
 await js(admin,`reinitOptsSeance()`);
 const opts=await js(admin,`optsSeanceHTML(false)`);
 assert.match(opts,/Salon[\s\S]*Chambre des parents/,'choix de l’Apple TV dans la fenêtre de lancement');
 assert.match(await js(admin,`argsOpts(false)`),/&appletv=AA%3ABB%3ACC%3ADD%3AEE%3A01/);
 await clic(admin,'#appareils-body [data-t="atv-activer"][data-v="AA:BB:CC:DD:EE:02"]');await toastVu(admin,'Apple TV utilisée pour les séances');
 await attendre(admin,`APP.etat.appletv.appareils.find(a=>a.active).identifiant==='AA:BB:CC:DD:EE:02'&&APP.etat.appletv.nom==='Chambre des parents'`);
 assert.match(await texte(admin,'#appareils-body .dcfg'),/Salon[\s\S]*Chambre des parents[\s\S]*Utilisée/,'l’ordre des appareils reste celui de leur enregistrement après changement de TV active');
 const cartesTV=await js(admin,`JSON.stringify({count:document.querySelectorAll('#appareils-body .atv-card').length,used:document.querySelector('#appareils-body .atv-current')?.textContent.trim(),action:getComputedStyle(document.querySelector('#appareils-body .atv-card .dcfg-actions .mini-btn')).minHeight,power:getComputedStyle(document.querySelector('#appareils-body .atv-card .dcfg-power .mini-btn')).minHeight})`).then(JSON.parse);
 assert.equal(cartesTV.count,2,'Les deux Apple TV utilisent le style de carte compact');assert.equal(cartesTV.used,'Utilisée');assert.equal(cartesTV.action,'32px');assert.equal(cartesTV.power,'32px','Les actions secondaires et alimentation sont compactes');
 ok('Deux Apple TV : seconde appairée sans remplacer la première, nom KamCiné, choix pour la séance, Apple TV active changée');

 // ---------- 2.6.108 : adresses enregistrées reprises par les accès rapides ----------
 const sec=JSON.parse(readFileSync(donnees+'/secrets.json','utf8'));
 sec.arr={radarr_hd:{url:'http://127.0.0.1:7878',cle:'fausse-cle-radarr-0123456789'}};
 writeFileSync(donnees+'/secrets.json',JSON.stringify(sec));
 await js(admin,`majArr().then(()=>{onglet('reglages');SET.cat='accueil';renderSettings()})`);
 await attendre(admin,`!!document.getElementById('reg-url_publique_radarr_hd')`);
 assert.equal(await js(admin,`document.getElementById('reg-url_publique_radarr_hd').placeholder`),'http://127.0.0.1:7878');
 assert.match(await texte(admin,'#reglages-body'),/Utilise l’adresse du service : http:\/\/127\.0\.0\.1:7878/);
 assert.equal(await js(admin,`urlServicePublic('radarr_hd')`),'http://127.0.0.1:7878','accès rapide sans nouvelle saisie');
 assert.equal(await js(admin,`urlServicePublic('sonarr_hd')`),'','service non configuré : pas d’adresse inventée');
 assert.equal(await js(admin,`document.body.innerHTML.includes('fausse-cle-radarr')`),false);
 await capture(admin,'acces_rapides');
 ok('Accès rapides : l’adresse enregistrée du service est reprise, aucune saisie en double, rien d’inventé');

 await send('Emulation.setTimezoneOverride',{timezoneId:'America/Los_Angeles'},admin);
 await js(admin,`majApropos().then(()=>{onglet('reglages');SET.cat='apropos';renderSettings()})`);
 assert.equal(await js(admin,`serverTimezone()`),'Asia/Kathmandu');
 assert.equal(await js(admin,`isoServer(Date.UTC(2026,0,15,12)/1000)`),'2026-01-15T17:45');
 assert.equal(await js(admin,`APR.fuseau`),'Asia/Kathmandu');
 const about=await texte(admin,'#reglages-body');
 assert.match(about,/GPL-3.0-only/);
 assert.match(about,/This product uses the TMDB API but is not endorsed or certified by TMDB\./);
 assert.match(about,/projet indépendant, non affilié/);
 assert.match(about,/Asia\/Kathmandu/);
 assert.equal(await js(admin,`document.querySelector('#reglages-body a[href="https://www.themoviedb.org"] img').getAttribute('src')`),'/logos/tmdb.svg');
 assert.equal(await js(admin,`lgHTML('imdb').includes('/logos/imdb.svg')&&lgHTML('imdb').includes('data-lg="IMDb"')`),true);
 for(const w of [320,390,1280]){await taille(admin,w,844);await pageVisible(admin,'credits_'+w)}
 await js(admin,`pollPlan()`);
 await attendre(admin,`PLAN.fuseau==='Asia/Kathmandu'`);
 ok('Crédits GPL et TMDB, mention indépendante, nom IMDb sans logo propriétaire, fuseau serveur distinct du téléphone, trois largeurs');

 // Régression de navigation : ouvrir réellement Programmation et téléchargements depuis la liste, puis revenir.
 await js(admin,`onglet('reglages')`);
 await clic(admin,'#set-cats [data-c="programmation"]');
 await sleep(100);
 const programmation=await js(admin,`JSON.stringify({cat:SET.cat,admin:estAdmin(),titre:document.querySelector('#reglages-body h1')?.textContent,body:document.querySelector('#reglages-body')?.innerText.slice(0,300),clic:document.querySelector('#set-cats [data-c="programmation"]')?.outerHTML.slice(0,180),erreurs:window.__errors||[]})`).then(JSON.parse);
 assert.equal(programmation.cat,'programmation','La catégorie doit ouvrir au clic : '+JSON.stringify(programmation));
 assert.ok(await js(admin,`!!document.querySelector('#reglages-body [data-k="plan_confirmation_requise"]')`),'Le panneau Programme rend ses réglages');
 assert.equal(await texte(admin,'#reglages-body h1'),'Programmation et téléchargements');
 assert.equal(await js(admin,`CATS.some(c=>c.id==='notifications-systeme')`),false,'la catégorie Notifications système dédiée a été retirée');
 await clic(admin,'#reglages-body [data-t="set-retour"]');
 await attendre(admin,`SET.cat===null&&!!document.querySelector('#set-cats [data-c="programmation"]')`);
 assert.equal(await js(admin,`document.querySelector('#set-cats [data-c="programmation"]').tagName`),'BUTTON','catégorie accessible comme bouton natif');
 ok('Programmation et téléchargements : ouverture réelle par toucher/clic, contenu affiché, retour fonctionnel');

 // Régression des cartes de catégories : description sous le titre, même si celui-ci revient sur deux lignes.
 await js(admin,`onglet('reglages');SET.cat=null;renderSettings()`);
 for(const w of [320,360,390,1280]){
  await taille(admin,w,844);
  const cartes=await js(admin,`(()=>[...document.querySelectorAll('#set-cats .cat-line')].map(c=>{const t=c.querySelector('.rt').getBoundingClientRect(),d=c.querySelector('.rd').getBoundingClientRect();return {cat:c.dataset.c,titre:{top:t.top,bottom:t.bottom,left:t.left,height:t.height},description:{top:d.top,bottom:d.bottom,left:d.left,height:d.height}}}))()`);
  assert.ok(cartes.length>5,'Les catégories de Réglages sont visibles');
  for(const c of cartes){assert.ok(c.description.top>=c.titre.bottom-1,'La description reste sous le titre (« '+c.cat+' », '+w+' px) : '+JSON.stringify(c));assert.ok(Math.abs(c.description.left-c.titre.left)<1,'Titre et description partagent le même alignement (« '+c.cat+' », '+w+' px)')}
  const programmation=cartes.find(c=>c.cat==='programmation');assert.ok(programmation,'Carte Programmation visible');
  if(w===320)assert.ok(programmation.titre.height>24,'Le titre Programmation peut occuper deux lignes à 320 px');
 }
 ok('Cartes Réglages : titres et descriptions verticalement séparés, alignement stable de 320 px au desktop');

 await js(admin,`SET.cat='securite';CPT.sous='profil';renderSettings()`);
 await fichierJpeg(admin,'/private/tmp/kc-avatar-auto-save.jpg',8,8,'#345678');
 const docPhoto=await send('DOM.getDocument',{},admin),noeudPhoto=await send('DOM.querySelector',{nodeId:docPhoto.root.nodeId,selector:'#file'},admin);
 assert.ok(noeudPhoto.nodeId,'Le sélecteur de fichier de profil existe');
 await send('DOM.setFileInputFiles',{nodeId:noeudPhoto.nodeId,files:['/private/tmp/kc-avatar-auto-save.jpg']},admin);
 await js(admin,`document.querySelector('#file').dispatchEvent(new Event('change',{bubbles:true}))`);
 await sleep(1500);
 const photoDebug=await js(admin,`JSON.stringify({compte:AUTH.compte,cat:SET.cat,sous:CPT.sous,file:$('file')?.files?.length,apercu:!!CPT.apercu,toast:$('toast')?.textContent,profil:!!document.querySelector('#reglages-body [data-t="cpt-photo-choisir"]')})`).then(JSON.parse);
 assert.ok(photoDebug.compte?.avatar_v&&photoDebug.profil,'Le sélecteur enregistre la photo automatiquement : '+JSON.stringify(photoDebug));
 assert.equal(await js(admin,`!!document.querySelector('#reglages-body [data-t="cpt-photo-ok"]')`),false,'Aucune confirmation Enregistrer la photo supplémentaire');
 assert.match(await texte(admin,'#reglages-body .cpt-tete'),/Visible par toi et par les administrateurs/,'Le profil affiche la photo réellement enregistrée');
 ok('Photo de profil : recadrage puis sauvegarde automatique, avatar confirmé par le serveur');

 assert.deepEqual(erreursJS,[],'Erreurs JavaScript : '+erreursJS.join('\n'));
 }
 console.log('\n'+resultats.length+' vérifications réussies');
}catch(e){console.error('ÉCHEC',e);process.exitCode=1}
finally{clearTimeout(minuterie);for(const p of [chrome,service])if(p&&p.pid){try{process.kill(-p.pid,'SIGKILL')}catch{p.kill('SIGKILL')}}for(const s of [PONT.srv,DSM.srv])if(s){s.closeAllConnections?.();s.close()}process.exit(process.exitCode||0)}
