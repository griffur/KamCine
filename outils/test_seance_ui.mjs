import {createServer} from 'node:http';
import {readFileSync,mkdtempSync,writeFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
const root=process.cwd();
let navigationFixture=false;
let resume=0,close=0,opening=0,pilotage=0,stops=0,notePerso=null;let ent=false;let notifications=[{id:"1",titre:"Avant",description:"Film",cree:1,lue:false}];
const meta={type:'movie',id:1,titre:'Toy Story 5',annee:'2026',duree:102,note:8.3};
let launch=null;
let film={actif:false,connecte:true,veille:false,app:'com.firecore.infuse',infuse_ouvert:true,meta:null};
let running=false,commandeErreur=false,ailleursResiste=false;const commandesRecues=[];
let derniereStart=null;
let planSeances=[];
const catalogueRequests=[];let cancelRequests=0;
const demandesEpisodes=[];
let collectionPreviewDelay=0,collectionPreviewCalls=0,collectionEtatsFailures=0,collectionEtatsRequests=0;
const catalogueItems=[
 {id:101,type:'movie',titre:'Film Alpha',annee:'2024',note:8.2,genres:[28],affiche:null},
 {id:102,type:'movie',titre:'Film Beta',annee:'2021',note:7.1,genres:[35],affiche:null},
 {id:201,type:'tv',titre:'Série Gamma',annee:'2025',note:9.0,genres:[18],affiche:null},
 {id:202,type:'tv',titre:'Série Delta',annee:'2019',note:6.4,genres:[35],affiche:null}
];
// Point 80 : réponses de fiche retenues pour observer les boutons pendant la vérification initiale.
const retenues={etat:[],episodes:[]};let retenir=false;
// Point 83 : réponse de /telechargements pilotée par le test (code HTTP et corps).
let dlReponse=null;
// Lot 2.6.81 : appels de la carte Séance interrompue et de la carte de fin.
const lot81={reprendre:[],fermer:0,finFermer:0,fermes:[]};let statusForce=null;
function lecteur(){
 const a=film.ailleurs||film,actif=!!(film.actif||film.ailleurs);
 return {actif,app:a.app,etat:a.etat,pos:a.pos||0,total:a.total||0,observe_a:Date.now()/1000,
  jeton:actif?(a.app||'Infuse')+':'+(a.titre||a.meta?.titre||'Film'):'',
  commandes:actif?(a.commandes||{play_pause:true,back:true,forward:true}):{}};
}
function state(){
 const phase=ent?'entracte':film.actif?(film.etat==='Paused'?'pause':'film'):'attente';
 const nature=running?'pilotee':film.actif?(launch?.confirme&&!launch?.suspendue?'liee':'detectee'):(launch?.suspendue||launch?.incertaine)?'memorisee':launch?'lancement':film.ailleurs?'ailleurs':'vide';
 return {nature,phase,commandes:!!film.actif&&!ent};
}
const server=createServer((req,res)=>{
 const p=new URL(req.url,'http://localhost').pathname;
 const url=new URL(req.url,'http://localhost');if(p.startsWith('/catalogue/'))catalogueRequests.push(req.url);
 if(navigationFixture&&p==='/tmdb/etat'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({ok:true,configuree:true}))}
 if(navigationFixture&&p==='/catalogue/chercher'){res.setHeader('Content-Type','application/json');const page=Number(url.searchParams.get('page'))||1,resultats=catalogueItems.filter(x=>x.titre.toLowerCase().includes((url.searchParams.get('q')||'').toLowerCase()));return res.end(JSON.stringify({ok:true,resultats:resultats.slice((page-1)*8,page*8),personnes:[],pages:Math.ceil(resultats.length/8),plus:page*8<resultats.length}))}
 res.setHeader('Content-Type',p==='/'?'text/html; charset=utf-8':'application/json');
 if(['/','/home','/catalogue','/downloads','/devices','/profile','/settings','/alerts','/logs','/suggestions'].includes(p)||/^\/(films|series|people)\/\d+$/.test(p)){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(readFileSync(root+'/app/index.html'))}
 if(p==='/lancement/reprendre'){lot81.reprendre.push(req.url);resume++;launch={...launch,suspendue:false,etat:'en_cours',confirme:false,phase:'Ouverture dans Infuse…'};return res.end(JSON.stringify({ok:true}))}
 if(p==='/lancement/fermer'){close++;lot81.fermes.push(req.url);if(!req.url.includes('t='))launch=null;return res.end(JSON.stringify({ok:true}))}
 // Lot 2.6.91 : limite des séances en suspens simulée (une réponse suspens_plein, puis succès sans changer l'état du test).
 if(p==='/reprendre'&&lot81.plein!==undefined){if(lot81.plein>0){lot81.plein--;res.statusCode=409;return res.end(JSON.stringify({ok:false,code:'suspens_plein',message:'3 séances sont déjà en suspens',suspendues:[{t:1111.5,type:'movie',id:3,meta:{titre:'Avatar'}},{t:2222.5,type:'movie',id:2,meta:{titre:'Jason Bourne'}},{t:1234.5,type:'tv',id:108978,saison:1,episode:6,meta:{titre:'Reacher'}}]}))}delete lot81.plein;return res.end(JSON.stringify({ok:true}))}
 if(p==='/appletv/infuse'){opening++;return res.end(JSON.stringify({ok:true}))}
 if(p==='/reprendre'){pilotage++;running=true;launch={type:'movie',id:1,origine:'reprise',etat:'ok',confirme:true,meta,jeton:1000};return res.end(JSON.stringify({ok:true}))}
 if(p==='/telecommande'){
  const q=new URL(req.url,'http://localhost').searchParams,cmd=q.get('cmd');commandesRecues.push(cmd);
  return setTimeout(()=>{
   if(commandeErreur)return res.end(JSON.stringify({ok:false,message:'Commande de test refusée'}));
   if(q.get('attendu')!==lecteur().jeton)return res.end(JSON.stringify({ok:false,message:'Média changé',lecteur:lecteur()}));
   const a=film.ailleurs||film;
   if(cmd==='play_pause')a.etat=a.etat==='Playing'?'Paused':'Playing';
   else a.pos=Math.max(0,(a.pos||0)+(cmd==='back'?-10:10));
   res.end(JSON.stringify({ok:true,lecteur:lecteur()}));
  },80);
 }
 if(p==='/stop'){stops++;lot81.stopUrl=req.url;if(lot81.libererApresStop){statusForce=lot81.libererApresStop;lot81.libererApresStop=null}running=false;launch=null;film={...film,actif:false,meta:null,etat:'Idle',ailleurs:ailleursResiste?film.ailleurs:null};return res.end(JSON.stringify({ok:true}))}
 if(p==='/start'){derniereStart=new URL(req.url,'http://localhost').search;return res.end(JSON.stringify({ok:true}))}
 if(p==='/seance/preparation/annuler'){cancelRequests++;return res.end(JSON.stringify({ok:true}));}
 if(p==='/notes-personnelles'&&req.method==='POST'){let b='';req.on('data',x=>b+=x);req.on('end',()=>{notePerso=JSON.parse(b).note;res.end(JSON.stringify({ok:true,note:notePerso}))});return}
 if(p.startsWith('/notes-personnelles/'))return res.end(JSON.stringify({ok:true,note:notePerso}));
 if(p==='/notifications/ouvrir'){notifications.forEach(n=>n.lue=true);return res.end(JSON.stringify({ok:true,notifications,non_lues:0}))}
 if(p==='/notifications/supprimer'){notifications=[];return res.end(JSON.stringify({ok:true,notifications,non_lues:0}))}
 if(p==='/status'&&statusForce)return res.end(JSON.stringify(Object.assign({ok:true,lecteur:lecteur(),occupation:''},statusForce)));
 // Point 68 (2.6.96) : programmation avec le sélecteur roulette, corps reçus conservés pour le test.
 if(p==='/planning/verifier')return setTimeout(()=>res.end(JSON.stringify({ok:true,libre:true,debut_txt:'lundi 28 septembre à 20:15',fin_txt:'22:40'})),lot81.verifDelai||0);
 if(p==='/planning/ajouter'||p==='/planning/modifier'){let b='';req.on('data',x=>b+=x);req.on('end',()=>{const c=JSON.parse(b||'{}');(lot81.plan=lot81.plan||[]).push({p,c});
  if(p==='/planning/ajouter')planSeances=[{pid:'n1',etat:'prevue',t:Date.now()/1000+7200,type:c.type,id:c.id,titre:'Toy Story 5',quand:c.quand,txt:'lundi 28 septembre à '+c.quand.slice(11),txt_court:'lun. 28 sept. '+c.quand.slice(11),mode:'reel',rappel_min:c.rappel_min,bandes_annonces:c.bandes_annonces,entracte:c.entracte}];
  res.end(JSON.stringify({ok:true}))});return}
 if(p==='/seance/fin/suggestions')return res.end(JSON.stringify({ok:true,resultats:[{type:'movie',id:12,titre:'Volet 3',annee:'2008',suite:true,raison:'Suite de la saga'},{type:'movie',id:20,titre:'Récent',annee:'2026'},{type:'tv',id:7,titre:'Série',annee:'2022'},{type:'movie',id:21,titre:'Récent 2',annee:'2026'},{type:'movie',id:22,titre:'Récent 3',annee:'2025'},{type:'movie',id:23,titre:'Récent 4',annee:'2025'},{type:'tv',id:24,titre:'Série 2',annee:'2024'},{type:'movie',id:25,titre:'Récent 5',annee:'2024'}]}));
 if(p==='/favoris/basculer'){lot81.favori=!lot81.favori;if(statusForce&&statusForce.fin_seance)statusForce.fin_seance.favori=lot81.favori;return res.end(JSON.stringify({ok:true,favori:lot81.favori}))}
 if(p==='/seance/fin/fermer'){lot81.finFermer++;if(statusForce?.fin_seance)statusForce.fin_seance=null;return res.end(JSON.stringify({ok:true}))}
 if(p==='/telechargements'&&dlReponse){const r=dlReponse;return setTimeout(()=>{res.statusCode=r.code;res.end(JSON.stringify(r.corps))},r.retard||0)}
 if(p==='/catalogue/genres')return res.end(JSON.stringify({ok:true,genres:url.searchParams.get('type')==='tv'?[{id:18,nom:'Drame'},{id:35,nom:'Comédie'}]:[{id:28,nom:'Action'},{id:35,nom:'Comédie'}]}));
 if(p==='/catalogue/genres/apercus')return res.end(JSON.stringify({ok:true,genres:[{id:28,nom:'Action',affiche:null},{id:18,nom:'Drame',affiche:null}]}));
 if(p==='/catalogue/parcourir'){
  let resultats=catalogueItems.filter(x=>x.type===url.searchParams.get('type'));
  if(url.searchParams.has('genre'))resultats=resultats.filter(x=>x.genres.includes(Number(url.searchParams.get('genre'))));
  if(url.searchParams.has('annee'))resultats=resultats.filter(x=>x.annee===url.searchParams.get('annee').replace('exact:',''));
  if(url.searchParams.get('tri')==='note')resultats.sort((a,b)=>b.note-a.note);else if(url.searchParams.get('tri')==='recents')resultats.sort((a,b)=>b.annee-a.annee);
  if(navigationFixture){const page=Number(url.searchParams.get('page'))||1;return res.end(JSON.stringify({ok:true,resultats:resultats.slice((page-1)*8,page*8),pages:Math.ceil(resultats.length/8)}))}
  return res.end(JSON.stringify({ok:true,resultats,pages:1}));
 }
 if(p==='/catalogue/genre')return res.end(JSON.stringify({ok:true,resultats:catalogueItems.filter(x=>x.genres.includes(Number(url.searchParams.get('id')))) ,pages:1}));
 if(['/catalogue/tendances','/catalogue/ajoutes','/catalogue/demandes','/catalogue/nouveautes'].includes(p)){
  const resultats=p==='/catalogue/demandes'?[catalogueItems[0],catalogueItems[2]]:p==='/catalogue/ajoutes'?[catalogueItems[0],catalogueItems[1]]:catalogueItems;
  return res.end(JSON.stringify({ok:true,resultats,plus:false,pages:1,pret:true}));
 }
 if(p==='/catalogue/etats'){
  const cles=url.searchParams.get('items').split(','),transitoire=cles.includes('movie-301')&&collectionEtatsRequests++<collectionEtatsFailures,etats={};for(const cle of cles){const serie=cle.startsWith('tv-'),dispo=cle.endsWith('101')||cle.endsWith('201')?'disponible':cle.endsWith('102')?'telechargement':'absent',hd=serie?'disponible':dispo,uhd=cle.endsWith('101')?'disponible':cle.endsWith('102')?'recherche':'absent';etats[cle]={cle,dispo,progres:cle.endsWith('102')?42:null,hd,uhd,hd_poster:hd==='disponible'?'available':hd==='telechargement'?'downloading':null,uhd_poster:uhd==='disponible'?'available':uhd==='recherche'?'requested':null,fiable:!transitoire}}
  return res.end(JSON.stringify({ok:true,etats,pret:!transitoire}));
 }
 if(p==='/catalogue/vus'){
  const vus={};for(const cle of url.searchParams.get('items').split(','))vus[cle]=cle.endsWith('101')||cle.endsWith('201');
  return res.end(JSON.stringify({ok:true,vus,pret:true}));
 }
 if(p==='/catalogue/collection/demander'){
  collectionPreviewCalls++;let body='';req.on('data',x=>body+=x);req.on('end',()=>setTimeout(()=>res.end(JSON.stringify({ok:true,titre:'Saga test',ids:[301,302,303,304],items:{},candidats:{hd:[302],uhd:[]},fiables:{hd:true,uhd:true}})),collectionPreviewDelay));return;
 }
 if(p==='/catalogue/episodes/demander'){
  let body='';req.on('data',x=>body+=x);req.on('end',()=>{const c=JSON.parse(body||'{}');demandesEpisodes.push(c);const nums=c.episode?[c.episode]:c.qualites.includes('hd')?[2,3]:[3];res.end(JSON.stringify({ok:true,resultats:c.qualites.map(q=>({qualite:q,episodes:nums}))}))});return;
 }
 if(p==='/catalogue/fiche'&&url.searchParams.get('type')==='tv')return res.end(JSON.stringify({ok:true,meta:{type:'tv',id:42,titre:'Paolo',tvdb_id:84,annee:'2026',synopsis:'Série de test'},saisons:[{numero:1,episodes:4,terminee:false}],casting:[],infos:[],genres:[],slogan:'',collection:null}));
 if(p==='/catalogue/fiche/etat'&&url.searchParams.get('type')==='tv'&&!retenir)return res.end(JSON.stringify({ok:true,rapide:true,statut:{hd:{etat:'partiel'},uhd:{etat:'partiel'},saisons:{'1':{hd:{etat:'partiel'},uhd:{etat:'partiel'}}},global:'partiel'},vu:false,vu_serie:false,profilage_ms:{}}));
 if(p==='/catalogue/episodes'&&url.searchParams.get('id')==='42'&&!retenir)return res.end(JSON.stringify({ok:true,episodes:[
  {numero:1,titre:'Départ',date:'2026-01-01',vu:true,versions:{hd:{etat:'disponible'},uhd:{etat:'disponible'}}},
  {numero:2,titre:'Paolo',date:'2026-01-08',vu:false,versions:{hd:{etat:'disponible'},uhd:{etat:'absent'}}},
  {numero:3,titre:'Suite',date:'2026-01-15',vu:false,versions:{hd:{etat:'absent'},uhd:{etat:'absent'}}},
  {numero:4,titre:'À venir',date:'2099-01-01',vu:false,versions:{hd:{etat:'a_venir'},uhd:{etat:'a_venir'}}}
 ]}));
 if(retenir&&p==='/catalogue/fiche/etat'&&!new URL(req.url,'http://localhost').searchParams.has('rapide')){res.u=req.url;retenues.etat.push(res);return}
 if(retenir&&p==='/catalogue/episodes'){res.u=req.url;retenues.episodes.push(res);return}
 const data={
 '/auth/etat':{ok:true,etat:'connecte',version:'2.7.10',compte:{id:1,identifiant:'test',nom:'Test',role:'admin',admin:true,actif:true,avatar_v:null,bienvenue:false,permissions:{demander:true,seances:true,telechargements:true}}},
 '/comptes':{ok:true,comptes:[{id:1,identifiant:'test',nom:'Test',role:'admin',admin:true,actif:true,sessions:1}],moi:1},
 '/planning':Object.assign({ok:true,seances:planSeances,rappels:[],attentes:[],maintenant:Date.now()/1000},lot81.planHeure||{}),
 '/reglages':{ok:true,icone:'sombre',nom:'Test',nb_trailers:2,entracte_actif:true},'/reglages/defauts':{ok:true},
 '/status':{ok:true,lecteur:lecteur(),lecture:film,seance:state(),occupation:'lecture:1000',en_cours:running,contenu:running?meta:null,etapes:running?[{cle:'preparation',nom:'Préparation',etat:'fait'},{cle:'bandes_annonces',nom:'Bandes annonces',etat:'saute'},{cle:'film',nom:'Film',etat:ent||film.etat==='Paused'?'pause':'en_cours'},{cle:'entracte',nom:'Entracte',etat:ent?'en_cours':'fait'},{cle:'generique',nom:'Générique',etat:'a_venir'}]:[],detail:{entracte:{etat:ent?'actif':'fait'}},mode:'reel',mode_reglage:'reel',lancement:launch},
 '/profil/resume':{ok:true,source:'KamCiné',stats:{mois:[],films:0,episodes:0,minutes:0,seances:0},films:[],series:[],favoris:{films:[],series:[]}},
 '/catalogue/fiche':{ok:true,meta,infos:[],casting:[],saisons:[]},'/catalogue/fiche/etat':{ok:true,statut:{hd:{etat:'disponible',fichier:'Un.fichier.tres.long.avec.des.informations.techniques.2160p.2026.mkv'},uhd:{etat:'absent'},global:'disponible'}},'/film':film,'/notifications':{ok:true,notifications,non_lues:notifications.filter(n=>!n.lue).length},
 '/catalogue/demandes':{ok:true,resultats:[],plus:false},'/catalogue/episodes':{ok:true,episodes:[{numero:5,titre:'Épisode 5'}]},'/arr/etat':{ok:true,instances:[]}
 };
 res.end(JSON.stringify(data[p]||{ok:true,configuree:false,resultats:[]}));
});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(8897,'127.0.0.1',resolve)});
const chrome=spawn(process.env.KAMCINE_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-pipe','--user-data-dir='+mkdtempSync('/private/tmp/kc-plan-chrome-'),'about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe']});
 let id=0,buffer='',session;const pending=new Map(),errors=[],browserDiagnostics=[];
chrome.stdio[4].on('data',chunk=>{buffer+=chunk.toString();let pos;while((pos=buffer.indexOf('\0'))>=0){const m=JSON.parse(buffer.slice(0,pos));buffer=buffer.slice(pos+1);if(m.id){const cb=pending.get(m.id);pending.delete(m.id);m.error?cb.reject(m.error):cb.resolve(m.result)}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Runtime.consoleAPICalled'&&m.params.type==='error')errors.push(m.params.args.map(x=>x.value||x.description||'').join(' '));if(m.method==='Log.entryAdded'&&m.params.entry.level==='error')browserDiagnostics.push(m.params.entry.text)}});
function send(method,params={},sid=session){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});chrome.stdio[3].write(JSON.stringify({id:n,method,params,...(sid?{sessionId:sid}:{})})+'\0')})}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function js(expression){expression="window.retourFicheTest=async()=>{if(!FICHE_OUVERTE)return;retourNav();await new Promise(r=>setTimeout(r,200));while(NAV.restoring)await new Promise(r=>setTimeout(r,20))};"+expression;const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,replMode:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value}
const timer=setTimeout(()=>{console.error('Expiration du test navigateur');chrome.kill();server.close();process.exit(1)},240000);
try{
 const {targetId}=await send('Target.createTarget',{url:'about:blank'});({sessionId:session}=await send('Target.attachToTarget',{targetId,flatten:true}));
 await send('Runtime.enable');await send('Page.enable');await send('Log.enable');
 await send('Page.navigate',{url:'http://127.0.0.1:8897'});await sleep(1200);
 for(let i=0;i<100&&!(await js('NAV.ready&&!NAV.restoring'));i++)await sleep(100);
 assert.equal(await js('NAV.ready&&!NAV.restoring'),true);
 if(!process.env.KAMCINE_NAV_ONLY&&!process.env.KAMCINE_275_ONLY){
 await js("onglet('seance');await poll();await pollFilm()");
 assert.equal(await js("document.getElementById('btn-infuse').hidden"),false);
 assert.equal(await js("document.getElementById('btn-infuse').disabled"),true);
 await js("document.getElementById('btn-infuse').click()");assert.equal(opening,0);
 for(const variant of [{app:'com.google.youtube'},{app:null},{veille:true},{connecte:false}]){
  film={actif:false,connecte:true,veille:false,app:'com.firecore.infuse',meta:null,...variant};
  await js('await pollFilm()');assert.equal(await js("document.getElementById('btn-infuse').disabled"),false);
 }
 launch={type:'movie',id:1,saison:null,episode:null,origine:'lire',etat:'echec',meta,message:'Non confirmé',jeton:1000};
 film={actif:false,connecte:true,veille:false,app:'com.firecore.infuse',meta:null};
 await js('await poll();await pollFilm()');assert.equal(await js('etatAccueil()'),'lancement');
 launch={...launch,etat:'ok',confirme:true,suspendue:false};film={...film,actif:true,etat:'Paused',meta,total:6120,pos:123};
 await js('await poll();await pollFilm()');
 assert.equal(await js('etatAccueil()'),'libre');assert.equal(await js("document.getElementById('sc-badge').textContent"),'En pause');
 await js("lancementEchec('Ancienne réponse en erreur')");assert.equal(await js('LANC.etat'),'ok');
 launch={...launch,suspendue:true};film={...film,actif:false,etat:'Idle',meta:null};
 await js('await poll();await pollFilm()');
 assert.equal(await js('etatAccueil()'),'suspendue');
 await js("await ouvrirFiche('movie',1)");
 assert.equal(await js("!!document.querySelector('.pg-tt .pg-notes')"),true,'Les notes externes sont placées à droite de l’affiche');
 assert.equal(await js("document.querySelectorAll('.pg-note-perso').length"),1,'La note KamCiné est compacte et intégrée aux notes externes');
 assert.equal(await js("document.querySelectorAll('.pg-note-perso .note-modal-star').length"),0,'La fiche ne montre pas cinq étoiles');
 await js("document.querySelector('.pg-note-perso').click();await new Promise(r=>setTimeout(r,30))");
 assert.match(await js("$('m-titre').textContent"),/Quelle note pour Toy Story 5/);
 await js("document.querySelector('#m-extra [data-t=note-modal-star][data-note=\"3\"]').click();$('m-oui').click();await new Promise(r=>setTimeout(r,60))");assert.equal(notePerso,3,'La note de fiche utilise le stockage personnel commun');
 assert.equal(await js("document.querySelector('.pg-note-perso b')?.textContent"),'3','La note enregistrée se met à jour sur la fiche');
 for(const [width,notes,count] of [[320,{imdb:'—',rt_critiques:'N/A',metacritic:null},2],[360,{imdb:6.6,rt_critiques:null,metacritic:null},3],[390,{imdb:6.6,rt_critiques:76,metacritic:null},4]]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:2,mobile:true});
  await js(`FI.meta.titre=${JSON.stringify(width===320?'Titre court':"A Very Long Film Title That Wraps Into Two Lines")};FI.meta.annee='2025';FI.meta.note=6.2;FI.meta.affiche='/fake-poster.jpg';FI.genres=${JSON.stringify(width===360?['Action','Aventure']:['Action','Aventure','Science-fiction'])};FI.notes=${JSON.stringify(notes)};NOTES_PERSO['movie:1']=3;renderFiche()`);
  const layout=await js("(()=>{const p=document.querySelector('.pg-poster').getBoundingClientRect(),t=document.querySelector('.pg-tt').getBoundingClientRect(),n=document.querySelectorAll('.pg-notes .note'),star=document.querySelector('.pg-note-perso .star').getBoundingClientRect(),value=document.querySelector('.pg-note-perso b').getBoundingClientRect(),external=document.querySelector('.pg-notes .note:not(.pg-note-perso) b');return {count:n.length,centerDiff:Math.abs((p.top+p.height/2)-(t.top+t.height/2)),starDiff:Math.abs((star.top+star.height/2)-(value.top+value.height/2)),personal:getComputedStyle(document.querySelector('.pg-note-perso b')).color,external:external?getComputedStyle(external).color:null,notesInHeader:!!document.querySelector('.pg-tt #fi-notes')}})()");
  assert.equal(layout.count,count,'Seules les notes disponibles s’affichent à '+width+' px');assert.ok(layout.notesInHeader,'La ligne des notes reste dans le bloc d’identité à '+width+' px');
  assert.ok(layout.centerDiff<12,'Identité verticalement centrée sur l’affiche à '+width+' px : '+JSON.stringify(layout));
  assert.ok(layout.starDiff<3&&layout.personal===layout.external,'Étoile et chiffre personnel alignés/couleur de note cohérente : '+JSON.stringify(layout));
  assert.ok(await js('document.documentElement.scrollWidth<=innerWidth'),'Pas de débordement horizontal à '+width+' px');
 }
 await js("FI.meta.titre='Toy Story 5';FI.meta.note=8.3;FI.meta.affiche=null;FI.genres=[];FI.notes={};NOTES_PERSO['movie:1']=3;renderFiche()");
 const atvHtml=await js("(()=>{APP.etat={appletv:{configuree:true,appareils:[{identifiant:'b',nom:'Bureau',active:false},{identifiant:'a',nom:'Salon',active:true}]}};APP.atv.mode='';DEV.appletv={etat:'allumee'};return atvConfigHTML(true)})()");
 assert.ok(atvHtml.indexOf('Bureau')<atvHtml.indexOf('Salon'),'L’ordre enregistré des Apple TV reste fixe');assert.match(atvHtml,/data-t="atv-power" data-id="a"/,'Commandes de réveil/veille ciblées');
 assert.match(atvHtml,/data-id="a" data-v="on" disabled aria-disabled="true"/,'Réveiller est désactivé lorsque la TV active est connue allumée');
 const veilleAtv=await js("(()=>{DEV.appletv={etat:'veille'};return atvConfigHTML(true)})()");assert.match(veilleAtv,/data-id="a" data-v="off" disabled aria-disabled="true"/,'Veille est désactivé lorsque la TV active est connue en veille');
 const etatAtvInconnu=await js("(()=>{DEV.appletv={etat:'injoignable'};return atvConfigHTML(true)})()");assert.doesNotMatch(etatAtvInconnu,/data-id="[ab]" data-v="(?:on|off)" disabled/,'État incertain : aucune commande n’est inventée comme active');
 await js('APP.etat=null');
 assert.equal(await js("document.querySelector('.pg-tt .sc-pill').textContent"),'À reprendre');
 assert.equal(await js("document.querySelector('[data-t=fi-encours]').textContent.trim()"),'À reprendre');
 launch={...launch,suspendue:false,incertaine:true};await js('await poll()');
 assert.equal(await js("document.querySelector('.pg-tt .sc-pill').textContent"),'État à vérifier');
 launch={...launch,suspendue:true,incertaine:false};await js('await poll()');
 assert.equal(await js("document.querySelector('.pg-tt .sc-pill').textContent"),'À reprendre');
 await js('await retourFicheTest()');
 assert.match(await js("document.getElementById('sc-etape').textContent"),/Séance interrompue/);
 assert.match(await js("document.getElementById('sc-titre').textContent"),/Toy Story 5/);
 assert.equal(await js("document.getElementById('btn-memoire').hidden"),false);
 assert.equal(await js("document.getElementById('sc-remote').classList.contains('hidden')"),true);
 for(const width of [320,375,414,768,1280]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
  assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true,'Débordement à '+width);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:375,height:850,deviceScaleFactor:1,mobile:true});
 const capture=await send('Page.captureScreenshot',{format:'png'});writeFileSync('/private/tmp/kc_infuse_reprise.png',Buffer.from(capture.data,'base64'));
 await send('Page.reload');await sleep(900);await js("onglet('seance');await poll();await pollFilm()");
 assert.equal(await js('etatAccueil()'),'suspendue');
 await js("document.getElementById('btn-memoire').click();document.getElementById('btn-memoire').click()");await sleep(200);
 assert.equal(resume,1);assert.equal(await js('LANC.etat'),'en_cours');
 launch={...launch,etat:'ok',confirme:true,suspendue:true};await js('await poll()');
 film={...film,actif:true,etat:'Playing',meta:{...meta,id:2,titre:'Autre film'}};await js('await pollFilm()');
 assert.equal(await js('etatAccueil()'),'libre');assert.match(await js("document.getElementById('sc-titre').textContent"),/Autre film/);
 film={...film,actif:false,meta:null};await js('await pollFilm()');
 await js("document.getElementById('btn-fermer').click()");await sleep(200);assert.equal(close,1);
 await js('await poll()');assert.equal(await js('LANC'),null);

 film={...film,actif:true,etat:'Playing',meta:null,total:0,pos:0};launch=null;
 await js('await poll()');
 assert.equal(await js('etatAccueil()'),'libre');
 // Lot 2.6.89 : Piloter attend l'identité du média, jamais une séance film supposée.
 assert.equal(await js("document.getElementById('btn-piloter').hidden"),true,'Piloter la séance attend l’identification du titre');
 film={...film,type:'tv',titre:'Reacher'};await js('await poll()');
 assert.equal(await js("document.getElementById('btn-piloter').hidden"),true,'Série prouvée mais épisode pas encore identifié : Piloter attend');
 assert.match(await js("$('sc-etape').textContent"),/Piloter la séance sera possible/);
 film={...film,type:null,titre:''};
 assert.doesNotMatch(await js("document.getElementById('sc-titre').textContent"),/Toy Story 5/);
 film={...film,meta,total:7200,pos:300};
 await js('await poll()');
 assert.match(await js("document.getElementById('sc-titre').textContent"),/Toy Story 5/,'Le titre identifié doit apparaître au relevé suivant, sans pause ni autre changement d’état');
 assert.equal(await js("document.getElementById('btn-piloter').hidden"),false,'Piloter disponible dès l’identification');

 film={...film,actif:true,etat:'Playing',meta,total:7200,pos:300};launch=null;
 await js('await poll()');
 assert.equal(await js("$('btn-stop').textContent"),'Arrêter');
 for(const width of [320,375,414,768,1280]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
  assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true);
  assert.equal(await js("$('btn-piloter').getBoundingClientRect().top===$('btn-stop').getBoundingClientRect().top"),true);
  assert.equal(await js("$('btn-stop').querySelector('span').getBoundingClientRect().height<30"),true);
  assert.equal(await js("$('btn-piloter').scrollWidth<=$('btn-piloter').clientWidth+1"),true);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:375,height:850,deviceScaleFactor:1,mobile:true});
 await js("$('sc-actions').scrollIntoView({block:'center'})");
 writeFileSync('/private/tmp/kc61_actions.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 const avantCommandes=commandesRecues.length;
 await js("await Promise.all([telecommande('back'),telecommande('forward'),telecommande('forward')])");
 assert.deepEqual(commandesRecues.slice(avantCommandes),['back','forward','forward']);
 assert.equal(film.pos,310);
 await js("await telecommande('play_pause')");
 assert.equal(await js('ST.lecteur.etat'),'Paused');
 assert.equal(await js("document.querySelector('#sc-remote [data-v=play_pause]').disabled"),false);
 await js("await telecommande('play_pause')");assert.equal(film.etat,'Playing');
 commandeErreur=true;const avantErreur=commandesRecues.length;
 await js("await Promise.all([telecommande('back'),telecommande('forward')])");
 assert.equal(commandesRecues.length,avantErreur+1);
 assert.equal(await js('lecteurEnvoi'),false);
 assert.equal(await js("[...document.querySelectorAll('#sc-remote button')].every(b=>!b.disabled)"),true);
 commandeErreur=false;
 // Lot 2.6.89, test B : fenêtre Piloter d'un épisode, sans option d'entracte ni texte de film.
 const filmAvant=film;
 film={...film,type:'tv',titre:'Reacher - S1 \u2219 E5 - Aucune excuse',meta:{type:'tv',id:108978,titre:'Reacher',annee:'2022',saison:1,episode:5,ep_titre:'Aucune excuse'},total:2869,pos:900};
 await js('await poll()');
 await js("$('btn-piloter').click()");await sleep(250);
 assert.equal(await js("document.querySelectorAll('#m-extra #pilotage-ent,#m-extra input').length"),0,'Aucune option pour un épisode');
 assert.match(await js("$('m-texte').textContent"),/l’épisode en cours \(Reacher, saison 1, épisode 5\)/);
 assert.doesNotMatch(await js("$('m-texte').textContent+$('m-extra').textContent"),/entracte|film/i);
 writeFileSync('/private/tmp/kc89_piloter_serie.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("$('m-non').click()");await sleep(250);
 film=filmAvant;await js('await poll()');
 // Test C : film, entracte en deux boutons segmentés (plus de boutons radio natifs), choix lisible.
 await js("$('btn-piloter').click()");await sleep(250);
 assert.equal(await js("document.querySelectorAll('#m-extra input[type=radio]').length"),0,'Plus de boutons radio natifs');
 assert.equal(await js("document.querySelectorAll('#pilotage-ent button').length"),2);
 assert.equal(await js("document.querySelector('#pilotage-ent button.on').dataset.v"),'1','Réglage par défaut : avec entracte');
 await js("document.querySelector('#pilotage-ent [data-v=\"0\"]').click()");
 assert.equal(await js("choixPilotage()"),'0');
 assert.equal(await js("document.querySelector('#pilotage-ent [data-v=\"0\"]').getAttribute('aria-checked')"),'true');
 for(const width of [320,375]){await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
  assert.ok(await js("[...document.querySelectorAll('#pilotage-ent button')].every(b=>b.scrollWidth<=b.clientWidth+1)&&$('pilotage-ent').getBoundingClientRect().right<=innerWidth"),'Choix de l’entracte lisible à '+width)}
 await send('Emulation.setDeviceMetricsOverride',{width:375,height:850,deviceScaleFactor:1,mobile:true});
 writeFileSync('/private/tmp/kc89_piloter_film.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("$('m-oui').click()");await sleep(200);
 assert.equal(pilotage,1);await js('await poll()');assert.equal(await js('etatAccueil()'),'seance');
 film.etat='Paused';await js('await poll()');assert.match(await js("$('sc-etape').textContent"),/film en pause/);
 for(const intermission of [true,false]){
  ent=intermission;film.actif=!ent;film.etat='Playing';await js('await poll()');
  assert.equal(await js("[...document.querySelectorAll('#sc-remote button')].every(b=>b.disabled)"),ent);
  if(ent){assert.equal(await js("$('btn-reprise').textContent.trim()"),'Arrêter l’entracte');assert.match(await js("$('sc-etape').textContent"),/Étape en cours : entracte/)}
  for(const width of [320,375,414,768,1280]){
   await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
   assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true,'Séance déborde à '+width);
   assert.equal(await js("[...document.querySelectorAll('#sc-actions button')].filter(b=>!b.hidden).every(b=>b.scrollWidth<=b.clientWidth+1)"),true,'Bouton déborde à '+width);
  }
  if(!intermission){
   // Entracte (secondaire) et Quitter la séance (rouge depuis le lot 2.6.89) sur une même ligne, sans débordement.
   for(const width of [320,375]){
    await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
    const rects=await js("JSON.stringify(['btn-entracte','btn-stop'].map(id=>document.getElementById(id).getBoundingClientRect()))").then(JSON.parse);
    assert.ok(rects[1].top>=rects[0].bottom,'Quitter la séance en bas, sous l’entracte, à '+width);
    assert.ok(await js("Math.abs($('btn-stop').getBoundingClientRect().width-$('sc-actions').getBoundingClientRect().width)<1"),'Quitter la séance en pleine largeur à '+width);
    assert.ok(rects.every(r=>r.height<=46),'Actions compactes à '+width);
   }
   assert.equal(await js("$('btn-stop').innerText.trim()"),'Quitter la séance');
   assert.equal(await js("getComputedStyle($('btn-stop')).backgroundColor"),'rgb(245, 9, 31)','Quitter la séance est rouge');
   assert.equal(await js("getComputedStyle($('btn-entracte')).backgroundColor!==getComputedStyle($('btn-stop')).backgroundColor"),true);
  }
 }
 film.actif=true;film.etat='Paused';film.total=0;ent=true;await js('await poll()');
 assert.equal(await js("[...document.querySelectorAll('#sc-remote button')].every(b=>!b.disabled)"),true,'Le lecteur Infuse revenu doit primer sur le marqueur entracte');
 await js("await telecommande('play_pause')");assert.equal(film.etat,'Playing');
 film.total=7200;ent=false;await js('await poll()');
 await js("await telecommande('back')");
 await send('Emulation.setDeviceMetricsOverride',{width:375,height:850,deviceScaleFactor:1,mobile:true});
 ent=true;film.actif=false;await js('await poll()');
 await js("$('sc-actions').scrollIntoView({block:'center'})");
 writeFileSync('/private/tmp/kc60_entracte.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 ent=false;film.actif=true;await js('await poll()');
 await js("$('ec-tete').click()");await sleep(150);
 assert.equal(await js('VUE'),'seance');assert.equal(await js('FICHE_OUVERTE'),true);
 assert.match(await js("$('fi-body').textContent"),/Un.fichier.tres.long/);
 for(const width of [320,375,414,768,1280]){await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});assert.equal(await js("$('fiche').scrollWidth<=$('fiche').clientWidth"),true)}
 await js('await retourFicheTest()');await sleep(200);assert.equal(await js('VUE'),'seance');assert.equal(await js('FICHE_OUVERTE'),false);
 await js("onglet('notifications')");assert.equal(await js("$('notifications-dot').classList.contains('hidden')"),true);await sleep(100);
 notifications.push({id:'2',titre:'Après',description:'Nouvelle',cree:2,lue:false});await js('await actualiserBadgeNotifications()');
 assert.equal(await js("$('notifications-dot').classList.contains('hidden')"),false);
 await js("Object.defineProperty(window,'isSecureContext',{value:false,configurable:true});Object.defineProperty(navigator,'userAgent',{value:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15',configurable:true});Object.defineProperty(navigator,'standalone',{value:false,configurable:true});await preparerPush()");
 assert.equal(await js("PUSH.state==='install'&&$('notify-push').disabled"),true,'Safari iPhone doit orienter vers l’installation');
 await js("Object.defineProperty(navigator,'standalone',{value:true,configurable:true});await preparerPush()");
 assert.equal(await js("PUSH.state==='https'&&$('notify-push').disabled&&$('notify-push-note').textContent.includes('HTTPS')"),true,'La PWA sur HTTP doit expliquer la vraie limite HTTPS');
 await js(`Object.defineProperty(window,'isSecureContext',{value:true,configurable:true});window.PushManager=undefined;window.__pushOriginal=api;window.__pushRequests=[];window.__mockSub=null;window.__mockManager={getSubscription:async()=>window.__mockSub,subscribe:async()=>{window.__mockSub={endpoint:'https://push.example/iphone-current',toJSON:()=>({endpoint:'https://push.example/iphone-current',keys:{p256dh:'p',auth:'a'}}),unsubscribe:async()=>true};return window.__mockSub}};window.__mockReg={pushManager:window.__mockManager};Object.defineProperty(navigator,'serviceWorker',{value:{register:async()=>window.__mockReg,ready:Promise.resolve(window.__mockReg)},configurable:true});Object.defineProperty(window,'Notification',{value:{permission:'default',requestPermission:async()=>{window.__permissionRequests=(window.__permissionRequests||0)+1;window.Notification.permission='granted';return 'granted'}},configurable:true});api=(url,opt)=>{window.__pushRequests.push({url,opt});return Promise.resolve(url==='/notifications/push/cle'?{ok:true,j:{cle:'A'.repeat(43)}}:{ok:true,j:{message:'Le service Push a accepté l’envoi.'}})}`);
 await js('PUSH.registration=null;PUSH.key=null;PUSH.subscription=null;await preparerPush()');
 assert.equal(await js("PUSH.state==='permission'&&!$('notify-push').disabled&&$('notify-push-test')===null"),true,'Autorisation nécessaire ; le test est absent de la page Notifications');
 const boutonPush=await js("JSON.stringify((()=>{const r=$('notify-push').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,disabled:$('notify-push').disabled}})())").then(JSON.parse);assert.equal(boutonPush.disabled,false);
 await send('Input.dispatchMouseEvent',{type:'mousePressed',x:boutonPush.x,y:boutonPush.y,button:'left',clickCount:1});await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:boutonPush.x,y:boutonPush.y,button:'left',clickCount:1});
 for(let i=0;i<100&&!await js('!!PUSH.subscription');i++)await sleep(20);
 assert.equal(await js('!!PUSH.subscription'),true,'Le clic utilisateur crée l’abonnement PWA');
 assert.equal(await js("PUSH.state==='subscribed'&&$('notify-push-test')===null&&window.__permissionRequests===1"),true,'Le test n’encombre plus la page Notifications');
 await js("onglet('reglages');SET.cat='apparence';renderSettings()");
 assert.equal(await js("$('notify-push-test')&&!$('notify-push-test').classList.contains('hidden')"),true,'Le test appareil est présent dans Apparence et sons');
 await js("$('notify-push-test').click();new Promise(resolve=>setTimeout(resolve,20))");
 const pushOk=await js("JSON.stringify({payload:window.__pushRequests.find(x=>x.url==='/notifications/push/test'),saved:window.__pushRequests.find(x=>x.url==='/notifications/push'),key:window.__pushRequests.find(x=>x.url==='/notifications/push/cle'),note:$('notify-push-settings-note').textContent})").then(JSON.parse);
 assert.equal(pushOk.payload.url,'/notifications/push/test');assert.equal(JSON.parse(pushOk.payload.opt.body).endpoint,'https://push.example/iphone-current');
 assert.equal(JSON.parse(pushOk.saved.opt.body).endpoint,'https://push.example/iphone-current');assert.equal(pushOk.key.opt.headers['X-KamCine-Push-Subject'],'http://127.0.0.1:8897','L’origine de l’app est transmise dans un en-tête, sans URL contenant un secret');assert.match(pushOk.note,/Vérifie sa réception/);
 await js("api=()=>Promise.resolve({ok:false,status:503,j:{code:'configuration',message:'VAPID refusé : vérifie le sujet HTTPS.'}});await testerPush();api=window.__pushOriginal");
 assert.match(await js("$('notify-push-settings-note').textContent"),/VAPID refusé/,'Une erreur serveur de configuration est visible après le clic');
 await js("window.Notification.permission='denied';PUSH.subscription=null;await preparerPush()");
 assert.equal(await js("PUSH.state==='denied'&&$('notify-push').disabled&&$('notify-push-note').textContent.includes('Réglages')"),true,'Permission refusée clairement orientée vers Réglages');
 await js("api=window.__pushOriginal;window.__mockSub=null;PUSH.subscription={endpoint:'https://push.example/device'};afficherEtatPush('Notifications système activées','Cet appareil reçoit les événements importants uniquement.',false,'subscribed')");
 await js("api=()=>Promise.resolve({ok:false,j:{code:'abonnement_expire',message:'Cet abonnement a expiré. Réactive les notifications.'}});await testerPush();api=window.__pushOriginal");
 const pushExpired=await js("JSON.stringify({subscription:PUSH.subscription,hidden:$('notify-push-test').classList.contains('hidden'),note:$('notify-push-settings-note').textContent})").then(JSON.parse);
 assert.equal(pushExpired.subscription,null);assert.equal(pushExpired.hidden,true);assert.match(pushExpired.note,/a expiré/);assert.match(pushExpired.note,/nouveau/);
 for(const width of [320,390,768,1280]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:width<700});assert.equal(await js("$('notify-push-test').scrollWidth<=$('notify-push-test').clientWidth+1"),true,'Bouton notification déborde à '+width)}
 await js("onglet('notifications');document.querySelector('[data-t=notifications-clear]').click();await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(100);assert.equal(notifications.length,0);
 await js("onglet('telechargements');await chargerTelechargements();window.scrollTo(0,0);await new Promise(r=>requestAnimationFrame(r))");
 await js(`{const el=document.body;function event(t,y){const e=new Event(t,{bubbles:true,cancelable:true});Object.defineProperty(e,'touches',{value:[{clientX:100,clientY:y}]});el.dispatchEvent(e)}event('touchstart',100);event('touchmove',200)}`);
 assert.equal(await js("$('app-pull').textContent"),'Relâcher pour actualiser');
 await js("document.body.dispatchEvent(new Event('touchcancel',{bubbles:true}))");assert.equal(await js("$('app-pull').classList.contains('on')"),false);
 await js('actualiserVue()');await sleep(700);assert.equal(await js("$('app-pull').classList.contains('on')"),false);

 for(const vue of ['seance','appareils','notifications','profil','journal','reglages','catalogue']){
  await js('onglet('+JSON.stringify(vue)+');await actualiserVue()');
  assert.equal(await js("$('app-pull').classList.contains('on')"),false,'Badge non refermé : '+vue);
 }
 await js("onglet('seance');await poll();await ouvrirFiche('movie',2);demanderRemplacement(contenuEnSeance(),'Lire',()=>window.remplacementConfirme=true)");
 assert.equal(await js("$('m-titre').textContent"),'Séance déjà en cours');
 // Lot 2.6.92 : fiche compacte (affiche, titre, année et durée, position), texte court, Suspendre et lancer sur une ligne.
 assert.equal(await js("$('m-oui').textContent"),'Suspendre et lancer');
 assert.equal(await js("$('m-texte').textContent"),'La séance actuelle sera mise en suspens et pourra être reprise plus tard.');
 assert.equal(await js("document.querySelector('#m-extra .m-seance-txt b').textContent"),'Toy Story 5');
 assert.equal(await js("document.querySelector('#m-extra .m-seance-txt span').textContent"),'2026 · 1 h 42');
 assert.match(await js("document.querySelector('#m-extra .m-seance-pos').textContent"),/ sur 2:00:00$/);
 for(const width of [320,375,390]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  const b=await js("JSON.stringify(['m-non','m-oui'].map(id=>{const r=$(id).getBoundingClientRect();return [r.width,r.height,$(id).scrollWidth<=$(id).clientWidth+1]}))").then(JSON.parse);
  assert.ok(b[1][1]<=50&&b[1][2],'Suspendre et lancer sur une seule ligne à '+width+' : '+JSON.stringify(b));
  // Lot 2.6.93 : sous 360 px, les deux boutons se superposent (action principale au dessus) pour rester dans la fenêtre.
  if(width<360)assert.ok(await js("$('m-oui').getBoundingClientRect().bottom<=$('m-non').getBoundingClientRect().top&&$('m-oui').getBoundingClientRect().right<=document.querySelector('#modal .sheet').getBoundingClientRect().right"),'Boutons superposés dans la fenêtre à '+width);
  else assert.ok(b[1][0]>b[0][0]&&b[0][1]>=44&&b[0][0]>=88,'Annuler plus étroit mais confortable à '+width);
  assert.ok(await js("Math.abs(document.querySelector('.m-seance-aff').getBoundingClientRect().right-document.querySelector('.m-seance-txt').getBoundingClientRect().left)<=16"),'Titre collé à l’affiche à '+width);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 writeFileSync('/private/tmp/kc92_remplacement.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 assert.equal(stops,0);
 await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(900);
 assert.equal(stops,1);assert.equal(await js('window.remplacementConfirme'),true);
 await js('await retourFicheTest();await poll()');assert.equal(await js('etatAccueil()'),'vide');
 // Remplacement depuis une lecture ailleurs (YouTube) : une pause suffit, il ne faut pas attendre qu'elle
 // disparaisse de l'état (elle peut rester "ailleurs" un moment) avant de lancer le nouveau média.
 film={actif:false,connecte:true,ailleurs:{app:'com.google.ios.youtube',app_nom:'YouTube',titre:'Vidéo en cours',
  etat:'Playing',pos:5,total:180,commandes:{play_pause:true,back:true,forward:true},url:null}};
 ailleursResiste=true;
 await js('await poll();await ouvrirFiche(\'movie\',2)');
 await js("FI.meta.affiche='https://image.tmdb.org/t/p/w342/test-poster.jpg';renderFiche()");
 await js("document.querySelector('.pg-poster').click()");
 assert.equal(await js("$('image-viewer-img').src"),'https://image.tmdb.org/t/p/w780/test-poster.jpg');
 await js("$('image-viewer').click()");assert.equal(await js("$('image-viewer').hidden"),true);
 await js("ouvrirPersonne(77,{nom:'Personne test',photo:'https://image.tmdb.org/t/p/w342/test-profile.jpg'})");await sleep(100);
 await js("document.querySelector('.pe-ph').click()");
 assert.equal(await js("$('image-viewer-img').src"),'https://image.tmdb.org/t/p/w780/test-profile.jpg');
 await js("$('image-viewer-close').click();retourNav()");
 await js("demanderRemplacement(contenuEnSeance(),'Lire',ignorerAilleurs=>window.remplacementAilleursConfirme=ignorerAilleurs)");
 // Lot 2.6.95 : une lecture externe n'est jamais une séance : « Lecture en cours », l'application nommée, Arrêter et lire.
 assert.equal(await js("$('m-titre').textContent"),'Lecture en cours','Pas de séance qui n’existe pas');
 assert.match(await js("$('m-texte').textContent"),/^YouTube est actuellement en lecture sur l’Apple TV\./);
 assert.equal(await js("$('m-oui').textContent"),'Arrêter et lire');
 const avantStops=stops;
 const debutRemplacement=Date.now();
 await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(300);
 assert.equal(stops,avantStops+1);
 assert.equal(await js('window.remplacementAilleursConfirme'),true,"Le lancement doit continuer même si l'état affiche encore une lecture ailleurs");
 assert.ok(Date.now()-debutRemplacement<1000,'Ne doit pas attendre le scrutin de 8 s réservé à une vraie séance Infuse');
 // Point 77 : le remplacement confirmé (Lancer quand même) doit se voir jusqu'au /start réel, sinon le lancement
 // qui suit retombe sur le même conflit resté en pause (media_reel accepte aussi une pause), sans personne pour
 // le reconfirmer une deuxième fois.
 await js('await retourFicheTest();await poll();await ouvrirFiche(\'movie\',2)');
 derniereStart=null;
 await js("REG.confirm_delai=10;document.querySelector('[data-t=fi-seance]').click()");
 assert.equal(await js("$('m-titre').textContent"),'Lecture en cours');
 assert.equal(await js("$('m-oui').textContent"),'Arrêter et lancer');
 assert.doesNotMatch(await js("$('m-texte').textContent"),/suspens|séance actuelle/);
 // Lot 2.6.93 : une seule fenêtre, avec les options du nouveau média et le compte à rebours de Lancer la séance.
 assert.equal(await js("document.querySelectorAll('#m-extra [data-t=m-sw]').length"),2);
 assert.equal(await js("$('m-barbox').classList.contains('hidden')"),false,'Compte à rebours présent');
 await js("document.querySelector('#m-extra [data-k=ent]').click()");
 await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(400);
 assert.equal(await js("$('modal').classList.contains('show')"),false,'Aucune seconde fenêtre Lancer la séance');
 assert.match(derniereStart||'',/entracte=0/,'Les options choisies dans la fenêtre unique sont transmises');
 assert.match(derniereStart||'',/ignorer_ailleurs=1/,"Le /start reel doit porter la confirmation, sinon conflit_depart() rebloque sur la meme lecture ailleurs");
 ailleursResiste=false;film={...film,ailleurs:null};
 await js('await retourFicheTest();await poll()');
 launch={type:'movie',id:1,origine:'lire',etat:'ok',confirme:true,incertaine:true,meta,jeton:1000};
 await js('await poll()');assert.equal(await js("$('sc-badge').textContent"),'État à vérifier');
 assert.equal(await js("$('btn-memoire').hidden"),true);
 launch=null;
 film={actif:false,connecte:true,ailleurs:{app:'com.google.ios.youtube',app_nom:'YouTube',titre:'Une vidéo réellement en lecture',etat:'Playing',pos:10,total:180,commandes:{play_pause:true,back:true,forward:true},url:'https://www.youtube.com/watch?v=dQw4w9WgXcQ'}};
 await js('await poll()');assert.equal(await js('etatAccueil()'),'ailleurs');
 assert.equal(await js("$('ai-titre').textContent"),film.ailleurs.titre);
 assert.equal(await js("[...document.querySelectorAll('.ai-cmd button')].filter(b=>!b.hidden).length"),3);
 // Pas de bouton séparé : toute la zone titre/jaquette ouvre le contenu (adresse fiable disponible).
 assert.equal(await js("$('ai-corps').classList.contains('ouvrable')"),true);
 await js("window.__opened=[];window.open=u=>window.__opened.push(u)");
 await js("$('ai-corps').click()");
 assert.deepEqual(await js('window.__opened'),['https://www.youtube.com/watch?v=dQw4w9WgXcQ']);
 for(const width of [320,375,414,768,1280]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
  assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:375,height:850,deviceScaleFactor:1,mobile:true});
 await js("await retourFicheTest();$('ai-bloc').scrollIntoView({block:'start'})");await sleep(500);
 writeFileSync('/private/tmp/kc61_youtube.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 film.ailleurs={...film.ailleurs,etat:'Paused',commandes:{play_pause:true},url:null};await js('await poll()');
 assert.equal(await js("$('ai-st').textContent"),'En pause');
 assert.equal(await js("[...document.querySelectorAll('.ai-cmd button')].filter(b=>!b.disabled).length"),1);
 assert.equal(await js("$('ai-corps').classList.contains('ouvrable')"),false,'Sans adresse fiable, la zone ne doit plus se comporter comme un lien');
 await js("window.__opened=[];$('ai-corps').click()");
 assert.deepEqual(await js('window.__opened'),[],'Un clic sans adresse fiable ne doit rien ouvrir');
 film.ailleurs=null;await js('await poll()');assert.equal(await js('etatAccueil()'),'vide');
 assert.equal(await js("$('ai-bloc').classList.contains('hidden')"),true);
 // Une séance programmée manquée (occupation, ou filet de sécurité) reste gérable : Reprogrammer et Retirer.
 // planSeances (mock /planning) plutôt qu'une injection directe dans PLAN : ouvrirCreneau() relit /planning tout de
 // suite (pollPlan), une injection directe serait effacée par ce relevé avant même l'ouverture de la modal.
 const base=Math.floor(Date.now()/1000)-120;
 planSeances=[
  {pid:'m1',etat:'manquee',t:base,type:'movie',id:9,titre:'Film manqué',txt:'aujourd’hui à 20h00',mode:'reel',
   message:'Une autre séance est déjà en cours. La séance n’a pas été lancée.'},
  {pid:'s1',etat:'annulee',annulation_securite:true,t:base-300,type:'movie',id:8,titre:'Sans confirmation',txt:'aujourd’hui à 19h55',mode:'reel',
   message:'Aucune confirmation reçue à temps. Séance annulée ; aucun appareil activé.'}
 ];
 await js('await pollPlan()');
 assert.equal(await js("$('plan-card').classList.contains('hidden')"),false);
 assert.equal(await js("document.querySelectorAll('#plan-list .plan-item.ratee').length"),2);
 assert.equal(await js("[...document.querySelectorAll('#plan-list [data-t=plan-modifier]')].map(b=>b.textContent).join(',')"),'Reprogrammer,Reprogrammer');
 assert.equal(await js("[...document.querySelectorAll('#plan-list [data-t=plan-annuler]')].map(b=>b.textContent).join(',')"),'Retirer,Retirer');
 await js("ouvrirModifier('m1')");
 assert.equal(await js("$('m-titre').textContent"),'Reprogrammer la séance');
 await js("$('m-non').click()");
 await js("demanderAnnulation('s1')");
 assert.equal(await js("$('m-titre').textContent"),'Retirer la séance ?');
 await js("$('m-non').click()");
 planSeances=[];
 await js('await pollPlan()');
 // Point 80 : Lancer, Lire et Programmer restent inactifs tant que la vérification initiale de la fiche n'est pas finie.
 const actions="['fi-seance','fi-lire','fi-prog'].map(k=>document.querySelector('[data-t='+k+']').disabled).join(',')";
 // Répond à la requête retenue de la fiche visée, en l'attendant : une relance d'une fiche précédente peut arriver avant.
 async function repondre(file,id,corps){
  for(let n=0;n<100;n++){
   const i=retenues[file].findIndex(r=>new URL(r.u,'http://localhost').searchParams.get('id')===String(id));
   if(i>=0){retenues[file].splice(i,1)[0].end(JSON.stringify(corps));await sleep(200);return}
   await sleep(50);
  }
  throw new Error('Requête '+file+' '+id+' jamais reçue');
 }
 retenir=true;
 await js("await new Promise(r=>{addEventListener('popstate',()=>setTimeout(r,50),{once:true});retourNav();setTimeout(r,1500)});await ouvrirFiche('movie',31)");
 assert.equal(await js(actions),'true,true,true','Disponibilité pas encore connue : aucune action');
 assert.match(await js("document.querySelector('.pg-help').textContent"),/Vérification de la disponibilité/);
 derniereStart=null;
 await js("ficheVerifiee(FI)||document.querySelector('[data-t=fi-seance]').removeAttribute('disabled');document.querySelector('[data-t=fi-seance]').click();document.querySelector('[data-t=fi-prog]').removeAttribute('disabled');document.querySelector('[data-t=fi-prog]').click()");
 assert.ok(!['Lancer la séance','Programmer une séance'].includes(await js("$('m-titre').textContent")),'Un clic forcé avant la vérification ne doit rien ouvrir');
 await repondre('etat',31,{ok:true,statut:{incomplet:true,hd:{etat:'inconnu'},uhd:{etat:'inconnu'},global:'inconnu'}});
 assert.equal(await js(actions),'true,true,true','Statut incomplet sans version trouvée : on attend encore');
 await js("lancerP2(true,FI)");await sleep(100);
 await repondre('etat',31,{ok:true,statut:{hd:{etat:'disponible'},uhd:{etat:'absent'},global:'disponible'}});
 assert.equal(await js(actions),'false,false,false','Vérification terminée : les trois actions reviennent');
 await js("await new Promise(r=>{addEventListener('popstate',()=>setTimeout(r,50),{once:true});retourNav();setTimeout(r,1500)});await ouvrirFiche('movie',32)");
 await repondre('etat',32,{ok:true,statut:{incomplet:true,hd:{etat:'disponible'},uhd:{etat:'inconnu'},global:'disponible'}});
 assert.equal(await js(actions),'false,false,false','Une version déjà trouvée suffit, même si une autre liste manque encore');
 await js("await new Promise(r=>{addEventListener('popstate',()=>setTimeout(r,50),{once:true});retourNav();setTimeout(r,1500)});await ouvrirFiche('movie',33)");
 await repondre('etat',33,{ok:false,message:'Radarr injoignable'});
 assert.equal(await js(actions),'false,false,false','Une panne de disponibilité ne bloque pas indéfiniment');
 await js("await new Promise(r=>{addEventListener('popstate',()=>setTimeout(r,50),{once:true});retourNav();setTimeout(r,1500)});ouvrirFiche('tv',34)");await sleep(300);
 await repondre('etat',34,{ok:true,statut:{hd:{etat:'disponible'},uhd:{etat:'absent'},global:'disponible',saisons:{}}});
 assert.equal(await js(actions),'true,true,true',"Série : l'épisode visé n'est pas encore connu");
 await repondre('episodes',34,{ok:true,episodes:[{numero:1,titre:'Pilote'},{numero:2,titre:'Suite'}]});
 assert.equal(await js(actions),'false,false,false','Épisodes chargés : les actions reviennent');
 retenir=false;[...retenues.etat,...retenues.episodes].forEach(r=>r.end(JSON.stringify({ok:false})));retenues.etat=[];retenues.episodes=[];await js('await retourFicheTest()');
 // Point 83 : coupure passagère, vraie panne puis récupération, sans jamais vider les cartes déjà affichées.
 const torrent=progres=>({hash:'a'.repeat(40),nom:'Film A',etat:'téléchargement',progres,taille:1000,ajoute:1});
 const bandeau="(()=>{const b=document.querySelector('.dl-refresh-error');return b&&!b.classList.contains('hidden')?b.textContent:''})()";
 const cartes="document.querySelectorAll('#dl-content .dl-item').length";
 dlReponse={code:200,corps:{ok:true,configuree:true,active:true,en_cours:[torrent(40)],termines:[]}};
 await js("onglet('telechargements');await chargerTelechargements()");
 assert.equal(await js(cartes),1);assert.equal(await js(bandeau),'');
 dlReponse={code:200,corps:{ok:true,configuree:true,active:true,en_cours:[torrent(40)],termines:[],perime:true,age_s:12,cause:'delai'}};
 await js('await chargerTelechargements()');
 assert.match(await js(bandeau),/répond lentement.*12 s/,'Coupure passagère : données gardées et âge affiché');
 assert.equal(await js(cartes),1);
 dlReponse={code:502,corps:{ok:false,cause:'hors_ligne',message:'Transmission est injoignable à l’adresse enregistrée. Vérifie qu’il tourne sur le NAS.'}};
 await js('await chargerTelechargements()');
 assert.match(await js(bandeau),/injoignable/,'Vraie panne : message clair');
 assert.equal(await js(cartes),1,'Les dernières cartes restent visibles');
 // Lot 2.6.82 : pendant une actualisation lente, la page garde son dernier état valide à l'écran.
 dlReponse={code:200,retard:1500,corps:{ok:true,configuree:true,active:true,en_cours:[torrent(55)],termines:[]}};
 await js('window.dlLente=chargerTelechargements()');await sleep(400);
 assert.equal(await js(cartes),1,'Cartes gardées pendant la réponse lente');
 await js('await window.dlLente');
 assert.equal(await js(bandeau),'','Récupération : plus de bandeau');
 assert.match(await js("$('dl-content').textContent"),/55/,'Récupération : la progression est à jour');
 // Point 89 (2.6.100) : barre verte pour une progression normale ou terminée, rouge seulement en erreur, neutre en attente ou en pause.
 const tor=(n,etat,progres)=>({hash:String(n).repeat(40),nom:'Film '+n,etat,progres,taille:1000,ajoute:n});
 dlReponse={code:200,corps:{ok:true,configuree:true,active:true,termines:[{...tor(6,'terminé',100)}],
  en_cours:[tor(1,'téléchargement',42),tor(2,'erreur',30),tor(3,'en attente',0),tor(4,'en pause',15),tor(5,'vérification',60),tor(7,'seed',100)]}};
 await js('await chargerTelechargements()');
 const couleursDl=await js("JSON.stringify(Object.fromEntries([...document.querySelectorAll('#dl-content .dl-item')].map(x=>[x.querySelector('.dl-title').textContent,getComputedStyle(x.querySelector('.dl-bar i')).backgroundColor])))").then(JSON.parse);
 const vertDl='rgb(52, 195, 143)',rougeDl='rgb(239, 106, 106)',neutreDl='rgb(142, 151, 166)';
 assert.deepEqual(couleursDl,{'Film 1':vertDl,'Film 2':rougeDl,'Film 3':neutreDl,'Film 4':neutreDl,'Film 5':neutreDl,'Film 7':vertDl,'Film 6':vertDl},JSON.stringify(couleursDl));
 assert.notEqual(couleursDl['Film 1'],'rgb(245, 9, 31)','Plus de rouge pour une progression normale');
 // Un changement d'état en direct change la couleur (la carte est mise à jour sur place).
 dlReponse={code:200,corps:{ok:true,configuree:true,active:true,termines:[],en_cours:[tor(3,'téléchargement',5)]}};
 await js('await chargerTelechargements()');await sleep(400);
 assert.equal(await js("getComputedStyle(document.querySelector('#dl-content .dl-item .dl-bar i')).backgroundColor"),vertDl,'En attente puis téléchargement : vert');
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  assert.ok(await js("document.documentElement.scrollWidth<=innerWidth"),'Téléchargements sans débordement à '+width)}
 dlReponse=null;
 // Lot 2.6.81 : la séance conservée et la lecture réelle (YouTube) s'affichent ensemble, la bordure suit la séance.
 await js("await retourFicheTest();onglet('seance')");
 const conservee={t:1234.5,type:'movie',id:1,saison:null,episode:null,mode:'reel',incertaine:false,position:3100,total:7200,debut:Date.now()/1000-3600,meta:{titre:'Reacher',annee:'2022',affiche:null}};
 const youtube={app:'com.google.ios.youtube',app_nom:'YouTube',titre:'Une vidéo',etat:'Playing',pos:40,total:300,commandes:{play_pause:true}};
 statusForce={en_cours:false,lecture:{actif:false,connecte:true,ailleurs:youtube},seance:{nature:'ailleurs',phase:'lecture',commandes:false},seance_conservee:conservee,detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('sc-int').classList.contains('hidden')"),false,'Carte de la séance conservée visible');
 assert.equal(await js("$('ai-bloc').classList.contains('hidden')"),false,'YouTube reste visible dessous');
 // Lot 2.6.93 : rangée d'affiches (esprit Ajoutés récemment), sans bouton Play ni croix ; un toucher ouvre la fenêtre d'actions.
 assert.equal(await js("$('si-badge').textContent"),'Séance en suspens');
 assert.equal(await js("document.querySelectorAll('#si-liste .si-carte').length"),1);
 assert.equal(await js("document.querySelectorAll('#si-liste button, #si-liste .act-btn').length"),0,'Plus de gros boutons dans la rangée');
 assert.equal(await js("document.querySelector('#si-liste .si-carte b').textContent"),'Reacher');
 // Lot 2.6.94 : plus de barre de progression dans la rangée, même composant qu'Ajoutés récemment, juste au dessus de lui.
 assert.equal(await js("document.querySelectorAll('#si-liste .pb').length"),0,'Pas de barre de progression dans la rangée');
 assert.equal(await js("$('sc-int').classList.contains('home-rangee')&&$('home-ajoutes').classList.contains('home-rangee')"),true);
 assert.equal(await js("$('sc-int').nextElementSibling.id"),'home-ajoutes','Séances en suspens juste au dessus d’Ajoutés récemment');
 assert.ok(await js("$('ai-bloc').getBoundingClientRect().top<$('sc-int').getBoundingClientRect().top"),'La lecture actuelle reste le bloc principal en haut');
 for(const width of [320,375,390,414,768,1280]){await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});
  assert.ok(await js("document.documentElement.scrollWidth<=innerWidth&&$('sc-int').scrollWidth<=$('sc-int').clientWidth"),'Rangée sans débordement à '+width+' px')}
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 // Fenêtre d'actions : fiche compacte, Reprendre la séance (principale), Quitter la séance (secondaire), croix pour fermer.
 const reprisesAvant=lot81.reprendre.length;
 await js("document.querySelector('#si-liste .si-carte').click()");
 assert.equal(await js("$('m-titre').textContent"),'Séance en suspens');
 assert.equal(await js("$('m-x').classList.contains('hidden')"),false);
 assert.equal(await js("$('m-oui').textContent+'|'+$('m-non').textContent"),'Reprendre la séance|Quitter la séance');
 assert.match(await js("$('m-texte').textContent"),/YouTube.*remplacée/);
 assert.equal(await js("document.querySelector('#m-extra .m-seance-txt b').textContent"),'Reacher');
 assert.match(await js("document.querySelector('#m-extra .m-seance-pos').textContent"),/51:40 sur 2:00:00/);
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  assert.ok(await js("['m-non','m-oui'].every(id=>$(id).scrollWidth<=$(id).clientWidth+1&&$(id).getBoundingClientRect().height<=50&&$(id).getBoundingClientRect().right<=document.querySelector('#modal .sheet').getBoundingClientRect().right)"),'Boutons sur une ligne, dans la fenêtre à '+width);
  assert.ok(await js("$('m-oui').getBoundingClientRect().bottom<=$('m-non').getBoundingClientRect().top"),'Reprendre la séance au dessus de Quitter la séance à '+width)}
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 writeFileSync('/private/tmp/kc93_actions_suspens.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("$('m-x').click()");await sleep(300);
 assert.equal(await js("$('modal').classList.contains('hidden')"),true,'La croix ferme sans action');
 assert.equal(lot81.reprendre.length,reprisesAvant);
 await js("document.querySelector('#si-liste .si-carte').click()");await sleep(100);
 await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(300);
 assert.match(lot81.reprendre.at(-1)||'',/t=1234\.5.*remplacer=1/,'Reprend la séance existante, avec son jeton');
 // Lot 2.6.82 : dès la reprise demandée, le service renvoie la séance en cours (étape reprise) et plus de séance conservée.
 const etapesFilm=[{cle:'preparation',nom:'Préparation',etat:'fait',detail:''},{cle:'bandes_annonces',nom:'Bandes annonces',etat:'fait',detail:''},
  {cle:'film',nom:'Film',etat:'en_cours',detail:''},{cle:'entracte',nom:'Entracte',etat:'a_venir',detail:''},{cle:'generique',nom:'Générique',etat:'a_venir',detail:''}];
 statusForce={en_cours:true,controller_running:false,lecture:{actif:false,connecte:true},seance:{nature:'pilotee',phase:'reprise',etape:'reprise',commandes:false},seance_conservee:null,etapes:etapesFilm,contenu:{type:'movie',id:1,titre:'Reacher'},type:'film',detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('sc-int').classList.contains('hidden')"),true,'La carte compacte disparaît dès la reprise');
 assert.match(await js("$('sc-etape').textContent"),/Reprise de la séance/);
 // Lot 2.6.89 : une seule étape rouge (maintenant) ; terminé neutre avec une coche ; à venir gris.
 const couleurs=await js("JSON.stringify([...document.querySelectorAll('#sc-steps>li .tl-dot')].map(d=>getComputedStyle(d).backgroundColor))").then(JSON.parse);
 assert.equal(couleurs[2],'rgb(245, 9, 31)','Étape active en rouge');
 assert.equal(couleurs.filter(c=>c==='rgb(245, 9, 31)').length,1,'Une seule étape rouge');
 assert.notEqual(couleurs[0],'rgb(52, 195, 143)','Plus de vert pour une étape terminée');
 assert.equal(await js("!!document.querySelector('#sc-steps>li.done .tl-dot svg')"),true,'Coche sur l’étape terminée');
 // Pastille En pause : texte blanc sur fond orange.
 statusForce={...statusForce,seance:{nature:'pilotee',phase:'pause',etape:'film',commandes:true},lecture:{actif:true,etat:'Paused',connecte:true,total:7200,pos:100}};await js('await poll()');
 assert.equal(await js("$('sc-badge').textContent"),'En pause');
 // Lot 2.6.89, test G : générique d'un épisode. Épisode terminé (neutre, coché), Générique seule étape rouge, textes de série.
 const etapesSerie=(film,gen)=>[{cle:'preparation',nom:'Préparation',etat:'saute',detail:'Épisode déjà lancé'},{cle:'film',nom:'Épisode',etat:film,detail:''},{cle:'generique',nom:'Générique',etat:gen,detail:''}];
 const serieStatus={en_cours:true,controller_running:true,lecture:{actif:true,etat:'Playing',connecte:true,total:2869,pos:2760,type:'tv',meta:{type:'tv',id:108978,titre:'Reacher',saison:1,episode:5}},seance_conservee:null,contenu:{type:'tv',id:108978,titre:'Reacher',saison:1,episode:5,ep_titre:'Aucune excuse'},type:'serie',detail:{generique:{etat:'fait'}},lancement:null};
 statusForce={...serieStatus,seance:{nature:'pilotee',phase:'generique',etape:'generique',commandes:true},etapes:etapesSerie('fait','en_cours')};await js('await poll()');
 const couleursSerie=await js("JSON.stringify([...document.querySelectorAll('#sc-steps>li .tl-dot')].map(d=>getComputedStyle(d).backgroundColor))").then(JSON.parse);
 assert.deepEqual(couleursSerie.map(c=>c==='rgb(245, 9, 31)'),[false,false,true],'Pendant le générique, seule l’étape Générique est rouge');
 assert.equal(await js("!!document.querySelector('#sc-steps>li:nth-child(2) .tl-dot svg')"),true);
 assert.match(await js("$('sc-etape').textContent"),/générique.*épisode suivant/);
 assert.equal(await js("$('btn-stop').innerText.trim()"),'Quitter la séance');
 statusForce={...statusForce,seance:{nature:'pilotee',phase:'film',etape:'film',commandes:true},etapes:etapesSerie('en_cours','a_venir'),detail:{}};await js('await poll()');
 assert.match(await js("$('sc-etape').textContent"),/diffusion de l’épisode/);
 assert.doesNotMatch(await js("$('sc-bloc').innerText"),/Film en préparation|Film déjà lancé|diffusion du film|Film en cours/);
 statusForce={...statusForce,seance:{nature:'pilotee',phase:'pause',etape:'film',commandes:true},lecture:{...serieStatus.lecture,etat:'Paused'}};await js('await poll()');
 assert.match(await js("$('sc-etape').textContent"),/épisode en pause/);
 await send('Emulation.setDeviceMetricsOverride',{width:375,height:850,deviceScaleFactor:1,mobile:true});
 statusForce={...serieStatus,seance:{nature:'pilotee',phase:'generique',etape:'generique',commandes:true},etapes:etapesSerie('fait','en_cours')};await js('await poll()');
 await js("$('sc-steps').scrollIntoView({block:'center'})");
 writeFileSync('/private/tmp/kc89_generique_serie.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 // Pendant l'entracte d'un film, le film en pause n'est pas une seconde étape active.
 statusForce={...statusForce,type:'film',seance:{nature:'pilotee',phase:'entracte',etape:'entracte',commandes:false},etapes:[{cle:'preparation',nom:'Préparation',etat:'fait'},{cle:'bandes_annonces',nom:'Bandes annonces',etat:'fait'},{cle:'film',nom:'Film',etat:'pause',detail:'En pause pendant l’entracte'},{cle:'entracte',nom:'Entracte',etat:'en_cours'},{cle:'generique',nom:'Générique',etat:'a_venir'}],detail:{entracte:{etat:'actif'}}};await js('await poll()');
 assert.equal(await js("document.querySelectorAll('#sc-steps>li.act').length"),1,'Une seule étape active pendant l’entracte');
 // Lot 2.6.90 : ordre de la Home. Séance programmée aujourd'hui, puis séance en suspens, puis lecture actuelle.
 planSeances=[{pid:'p9',etat:'prevue',t:Math.floor(Date.now()/1000)+60,type:'movie',id:5,titre:'Toy Story 5',txt:'aujourd’hui',txt_court:'aujourd’hui',mode:'reel'}];
 const conserveeTv={t:1234.5,type:'tv',id:108978,saison:1,episode:6,mode:'reel',incertaine:false,position:483,total:2861,debut:Date.now()/1000-600,meta:{titre:'Reacher',annee:'2022',affiche:null}};
 statusForce={en_cours:false,lecture:{actif:false,connecte:true,ailleurs:{app:'com.google.ios.youtube',app_nom:'YouTube',titre:'Une vidéo',etat:'Playing',pos:40,total:300,commandes:{play_pause:true}}},seance:{nature:'ailleurs',phase:'lecture',commandes:false},seance_conservee:conserveeTv,detail:{},lancement:null};
 await js('await pollPlan();await poll()');
 assert.equal(await js("$('sv-proches').classList.contains('hidden')"),false);
 assert.equal(await js("$('sv-proches-t').textContent"),'Séance à venir aujourd’hui');
 const hauts=await js("JSON.stringify(['sv-proches','ai-bloc','sc-int'].map(id=>$(id).getBoundingClientRect().top))").then(JSON.parse);
 assert.ok(hauts[0]<hauts[1]&&hauts[1]<hauts[2],'Lot 2.6.94 : séance à venir, puis lecture actuelle, puis séances en suspens : '+hauts);
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await js('window.scrollTo(0,0)');
 writeFileSync('/private/tmp/kc90_ordre_home.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 planSeances=[];await js('await pollPlan()');
 // Même contenu en lecture et en suspens (même type, même TMDB) : jamais affiché deux fois, même avant Piloter.
 statusForce={...statusForce,lecture:{actif:true,etat:'Playing',connecte:true,total:2861,pos:2710,type:'tv',meta:{type:'tv',id:108978,titre:'Reacher',annee:'2022',saison:1,episode:6}},seance:{nature:'detectee',phase:'film',commandes:true}};await js('await poll()');
 assert.equal(await js("$('sc-int').classList.contains('hidden')"),true,'Pas de séance en suspens pour le contenu lu');
 statusForce={...statusForce,lecture:{...statusForce.lecture,meta:{type:'tv',id:108978,titre:'Reacher',annee:'2022',saison:1,episode:5}}};await js('await poll()');
 assert.equal(await js("$('sc-int').classList.contains('hidden')"),false,'S1E5 lu : la séance S1E6 reste en suspens');
 // Lot 2.6.91 : plusieurs séances en suspens, une ligne chacune, titre au pluriel ; Quitter vise la bonne séance.
 const avatarS={t:1111.5,type:'movie',id:3,mode:'reel',position:3600,total:9720,debut:Date.now()/1000-7200,meta:{titre:'Avatar',annee:'2009',affiche:null}};
 const jasonS={t:2222.5,type:'movie',id:2,mode:'reel',position:1200,total:7380,debut:Date.now()/1000-3600,meta:{titre:'Jason Bourne',annee:'2016',affiche:null}};
 statusForce={en_cours:false,lecture:{actif:false,connecte:true,contexte:{classe:'rien'}},seance:{nature:'vide',phase:'vide',commandes:false},seance_conservee:conserveeTv,seances_suspendues:[avatarS,jasonS],detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('si-badge').textContent"),'Séances en suspens');
 assert.deepEqual(JSON.parse(await js("JSON.stringify([...document.querySelectorAll('#si-liste .si-carte')].map(x=>x.querySelector(':scope > b').textContent+'|'+x.querySelector(':scope > span:last-child').textContent))")),['Reacher|S1 E6','Avatar|2009','Jason Bourne|2016']);
 for(const width of [320,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  assert.ok(await js("document.documentElement.scrollWidth<=innerWidth&&$('sc-int').scrollWidth<=$('sc-int').clientWidth"),'Rangée sans débordement à '+width);
  assert.ok(await js("new Set([...document.querySelectorAll('#si-liste .si-carte')].map(c=>Math.round(c.getBoundingClientRect().top))).size===1"),'Une seule rangée à '+width)}
 await js("window.scrollTo(0,0)");
 writeFileSync('/private/tmp/kc93_rangee.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 // Lot 2.6.95 : Quitter ferme la fenêtre d'actions puis ouvre une confirmation dédiée ; Annuler ne change rien.
 const fermesAvant=close;
 await js("document.querySelectorAll('#si-liste .si-carte')[1].click()");await sleep(100);
 assert.equal(await js("document.querySelector('#m-extra .m-seance-txt b').textContent"),'Avatar');
 await js("$('m-non').click()");await sleep(120);
 assert.equal(await js("$('modal').classList.contains('show')"),false,'La fenêtre d’actions se ferme d’abord');
 await sleep(250);
 assert.equal(await js("$('m-titre').textContent"),'Quitter la séance ?');
 assert.match(await js("$('m-texte').textContent"),/Confirmer l’arrêt de la séance « Avatar »/);
 assert.equal(await js("$('m-non').textContent+'|'+$('m-oui').textContent"),'Annuler|Quitter la séance');
 assert.equal(await js("$('m-oui').classList.contains('danger')"),true,'Action destructive');
 assert.equal(await js("document.querySelectorAll('#m-extra .m-seance').length+document.querySelectorAll('.overlay.show').length"),1,'Une seule fenêtre visible');
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  assert.ok(await js("['m-non','m-oui'].every(id=>$(id).scrollWidth<=$(id).clientWidth+1&&$(id).getBoundingClientRect().right<=document.querySelector('#modal .sheet').getBoundingClientRect().right)"),'Confirmation lisible à '+width)}
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 writeFileSync('/private/tmp/kc95_confirmation.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("$('m-non').click()");await sleep(300);
 assert.equal(close,fermesAvant,'Annuler ne quitte rien');
 assert.equal(await js("$('modal').classList.contains('show')"),false,'Annuler ferme seulement la confirmation');
 await js("document.querySelectorAll('#si-liste .si-carte')[1].click()");await sleep(100);
 await js("$('m-non').click()");await sleep(400);
 await js("$('m-oui').click()");await sleep(300);
 assert.equal(close,fermesAvant+1);
 assert.match(lot81.fermes.at(-1)||'',/t=1111\.5/,'Quitter vise Avatar, pas la séance active');
 // Limite : le service répond suspens_plein, Alex choisit la séance à quitter, puis l'action reprend.
 lot81.plein=1;
 await js("window.__pr=null;apiPlace('/reprendre?entracte=0',{method:'POST'}).then(r=>{window.__pr=r});0");await sleep(300);
 assert.equal(await js("$('m-titre').textContent"),'Trop de séances en suspens');
 assert.deepEqual(JSON.parse(await js("JSON.stringify([...document.querySelectorAll('[data-modal-choice]')].map(b=>b.textContent))")),['Quitter Avatar','Quitter Jason Bourne','Quitter Reacher, S1E6','Annuler']);
 await js("document.querySelector('[data-modal-choice=\"1\"]').click()");
 await sleep(400);
 assert.equal(await js("window.__pr&&window.__pr.ok"),true,'Action reprise après avoir quitté une séance');
 assert.match(lot81.fermes.at(-1)||'',/t=2222\.5/);
 // Netflix lit sans transmettre de titre : lecture honnête, aucun titre inventé.
 statusForce={en_cours:false,lecture:{actif:false,connecte:true,ailleurs:{app:'com.netflix.Netflix',app_nom:'Netflix',titre:'',etat:'Playing',pos:0,total:0,commandes:{}},contexte:{classe:'lecture',app:'com.netflix.Netflix',app_nom:'Netflix'}},seance:{nature:'ailleurs',phase:'lecture',commandes:false},seance_conservee:conserveeTv,detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('ai-titre').textContent"),'Lecture dans Netflix');
 assert.match(await js("$('ai-sub').textContent"),/ne transmet pas le titre/);
 // Netflix ouvert sans lecture : Rien en lecture actuellement, et la dernière application signalée (jamais « Dans Netflix »).
 statusForce={...statusForce,lecture:{actif:false,connecte:true,ailleurs:null,contexte:{classe:'app_sans_lecture',app:'com.netflix.Netflix',app_nom:'Netflix'}},seance:{nature:'vide',phase:'vide',commandes:false}};await js('await poll()');
 assert.equal(await js("$('sc-vide').classList.contains('hidden')"),false);
 assert.equal(await js("$('vide-app').textContent"),'Dernière application signalée : Netflix');
 assert.ok(await js("$('sc-vide').getBoundingClientRect().top<$('sc-int').getBoundingClientRect().top"),'Rien en lecture reste le bloc principal, les séances en suspens dessous');
 writeFileSync('/private/tmp/kc90_suspens_vide.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 statusForce={...statusForce,type:'film',seance:{nature:'pilotee',phase:'pause',etape:'film',commandes:true},lecture:{actif:true,etat:'Paused',connecte:true,total:7200,pos:100},etapes:etapesFilm,detail:{}};await js('await poll()');
 assert.equal(await js("getComputedStyle($('sc-badge')).color"),'rgb(255, 255, 255)');
 statusForce={en_cours:false,lecture:{actif:false,connecte:true,ailleurs:youtube},seance:{nature:'ailleurs',phase:'lecture',commandes:false},seance_conservee:conservee,detail:{},lancement:null};await js('await poll()');
 const fermeturesAvant=close;
 await js("document.querySelector('#si-liste .si-carte').click()");await sleep(100);
 await js("$('m-non').click()");await sleep(400);
 await js("$('m-oui').click()");await sleep(300);
 assert.equal(close,fermeturesAvant+1,'Quitter termine la séance mémorisée après confirmation');
 // Lot 2.6.84 : la pastille du bloc principal n'est jamais collée au titre, avec ou sans image de fond, quel que soit l'état.
 const ecart="$('sc-titre').getBoundingClientRect().top-$('sc-badge').getBoundingClientRect().bottom";
 for(const [nom,lec,seance] of [['lecture',{actif:true,etat:'Playing',titre:'Reacher',type:'tv',meta:{type:'tv',id:7,titre:'Reacher',annee:'2022',saison:1,episode:3}},{nature:'detectee',phase:'film',commandes:true}],
                               ['pause',{actif:true,etat:'Paused',titre:'Reacher',type:'tv',meta:{type:'tv',id:7,titre:'Reacher',saison:1,episode:3}},{nature:'detectee',phase:'pause',commandes:true}],
                               ['neutre',{actif:true,etat:'Playing',titre:'Reacher',type:null,meta:null},{nature:'detectee',phase:'film',commandes:true}],
                               ['pilotee',{actif:true,etat:'Playing',titre:'Reacher',type:'tv',meta:{type:'tv',id:7,titre:'Reacher',saison:1,episode:3}},{nature:'pilotee',phase:'film',etape:'film',commandes:true}]]){
  statusForce={en_cours:nom==='pilotee',lecture:{connecte:true,total:2789,pos:900,...lec},seance,seance_conservee:null,etapes:nom==='pilotee'?[{cle:'preparation',nom:'Préparation',etat:'fait',detail:''},{cle:'film',nom:'Épisode',etat:'en_cours',detail:''},{cle:'generique',nom:'Générique',etat:'a_venir',detail:''}]:null,contenu:nom==='pilotee'?{type:'tv',id:7,titre:'Reacher',saison:1,episode:3}:null,type:'serie',detail:{},lancement:null};
  await js('await poll()');
  for(const width of [320,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
   assert.ok(await js(ecart)>=12,'Pastille décollée du titre ('+nom+', '+width+' px) : '+await js(ecart))}
 }
 statusForce={en_cours:false,lecture:{actif:false,connecte:true,ailleurs:{app:'com.google.ios.youtube',app_nom:'YouTube',titre:'Une vidéo',etat:'Playing',pos:40,total:300}},seance:{nature:'ailleurs',phase:'lecture',commandes:false},seance_conservee:null,detail:{},lancement:null};await js('await poll()');
 assert.ok(await js("$('ai-app').getBoundingClientRect().top-$('ai-st').getBoundingClientRect().bottom")>=12,'Pastille de la lecture externe décollée du titre');
 // Lot 2.6.83 : une lecture Infuse pas encore identifiée n'invente jamais un scénario de film.
 statusForce={en_cours:false,lecture:{actif:true,etat:'Playing',connecte:true,titre:'Cuillère en argent',type:null,meta:null,total:2800,pos:900},seance:{nature:'detectee',phase:'film',commandes:true},seance_conservee:null,detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('sc-titre').textContent"),'Lecture en cours');
 assert.equal(await js("document.querySelectorAll('#sc-steps>li').length"),0,'Aucun scénario tant que le type est inconnu');
assert.doesNotMatch(await js("$('sc-bloc').innerText"),/Film en cours|Bandes annonces|Entracte/);
 // Identification tardive : le scénario série apparaît de lui même, sans relancer l'épisode.
 statusForce={...statusForce,lecture:{actif:true,etat:'Playing',connecte:true,titre:'Cuillère en argent',type:'tv',total:2800,pos:900,meta:{type:'tv',id:7,titre:'Reacher',annee:'2022',saison:1,episode:3,ep_titre:'Cuillère en argent'},serie:{nom:'Reacher',saison:1,episode:3}}};await js('await poll()');
 assert.match(await js("$('sc-titre').textContent"),/Reacher/);
 assert.deepEqual(JSON.parse(await js("JSON.stringify([...document.querySelectorAll('#sc-steps .tl-t')].map(x=>x.textContent))")),['Préparation','Épisode','Générique']);
 // Lot 2.6.85 : titre réel d'Infuse reconnu comme épisode par le service : scénario série, jamais film.
 statusForce={...statusForce,lecture:{actif:true,etat:'Playing',connecte:true,titre:'Reacher - S1 \u2219 E4 - Dommages collatéraux',type:'tv',total:2716,pos:900,meta:null,serie:{nom:'Reacher',saison:1,episode:4,ep_titre:'Dommages collatéraux'}}};await js('await poll()');
 assert.deepEqual(JSON.parse(await js("JSON.stringify([...document.querySelectorAll('#sc-steps .tl-t')].map(x=>x.textContent))")),['Préparation','Épisode','Générique']);
 assert.match(await js("$('sc-sub').textContent"),/Saison 1, épisode 4/);
 assert.equal(await js("memeMediaAccueil({type:'tv',id:7,saison:1,episode:3},{type:'tv',id:7,saison:1,episode:4})"),false,'S1E4 n\'est pas la séance S1E3');
 assert.equal(await js("memeMediaAccueil({type:'tv',id:7,saison:1,episode:3},{type:'tv',id:7,saison:1,episode:3})"),true);
 // Carte de fin : jamais une séance active, suite de saga en premier, fermable.
 const fin={t:Date.now()/1000,type:'movie',id:1,mode:'reel',favori:false,meta:{titre:'Barbare',annee:'2022',affiche:null}};
 statusForce={en_cours:false,lecture:{actif:false,connecte:true},seance:{nature:'vide',phase:'vide',commandes:false},seance_conservee:null,fin_seance:fin,detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('fin-bloc').classList.contains('hidden')"),false);
 assert.equal(await js("$('sc-int').classList.contains('hidden')"),true);
 assert.equal(await js("$('fin-q').textContent"),'Avez-vous aimé ce film ?');
 await sleep(300);
 assert.ok(await js("document.querySelectorAll('#fin-sugg .cat-card').length")<=8);
 // La rangée défile horizontalement et suggère d'autres contenus sans limiter le total à quatre.
 assert.equal(await js("new Set([...document.querySelectorAll('#fin-sugg .cat-card')].map(c=>Math.round(c.getBoundingClientRect().top))).size"),1,'Une seule rangée');
 assert.equal(await js("getComputedStyle($('fin-sugg')).overflowX"),'auto');
 assert.equal(await js("!!document.querySelector('#fin-sugg [data-t=fin-sugg-more]')"),true,'Une action Voir plus termine le carrousel');
 await js("document.querySelector('#fin-sugg [data-t=fin-sugg-more]').click();await new Promise(r=>setTimeout(r,80))");
 assert.equal(await js("VUE"),'fin-suggestions','Voir plus ouvre une vraie vue KamCiné');
 assert.match(await js("$('fin-suggestions-title').textContent"),/^À regarder ensuite/);
 assert.ok(await js("document.querySelectorAll('#fin-suggestions-grid .cat-card').length")<=30,'La vue étendue est limitée à trente recommandations');
 await js("document.querySelector('#v-fin-suggestions [data-t=fin-sugg-back]').click();await new Promise(r=>setTimeout(r,20))");
 assert.equal(await js("VUE"),'seance','Le retour retrouve l’écran de fin de séance');
 assert.equal(await js("$('sc-bloc').classList.contains('hidden')&&$('sc-vide').classList.contains('hidden')"),true,'La carte de fin remplace le bloc de séance');
 // La carte de fin ne duplique plus le contrôle Favori.
 assert.equal(await js("!!$('fin-fav')"),false,'Pas de cœur dans la carte de fin');
 assert.equal(await js("!!document.querySelector('.fin-close')"),true,'Croix discrète en haut à droite');
 assert.equal(await js("document.querySelectorAll('.fin-act').length"),0,'Pas de gros bouton Fermer en bas');
 await js("document.querySelector('[data-t=fin-note][data-note=\"4\"]').click();await new Promise(r=>setTimeout(r,30))");
 assert.equal(notePerso,4,'La note personnelle est persistée');assert.equal(await js("document.querySelectorAll('.fin-star.on').length"),4);
 await js("document.querySelector('[data-t=fin-note][data-note=\"2\"]').click();await new Promise(r=>setTimeout(r,30))");
 assert.equal(notePerso,2,'La nouvelle note remplace la précédente');assert.equal(await js("document.querySelectorAll('.fin-star.on').length"),2);
 await js("onglet('seance');$('profil-body').innerHTML='';window.scrollTo(0,0);await ouvrirFiche('movie',1);await new Promise(r=>setTimeout(r,60));window.__fetchOrig=fetch;window.__profilResolve=null;window.fetch=(...a)=>new URL(a[0],location.href).pathname==='/profil/resume'?new Promise(resolve=>window.__profilResolve=()=>resolve(new Response(JSON.stringify({ok:true,source:'KamCiné',stats:{mois:[],films:0,episodes:0,minutes:0,seances:0},films:[],series:[],favoris:{films:[],series:[]}}),{status:200,headers:{'Content-Type':'application/json'}}))):window.__fetchOrig(...a);document.querySelector('[data-t=fi-favori]').click();await new Promise(r=>setTimeout(r,200))");
 assert.equal(await js("$('toast').dataset.t"),'toast-favoris','Le toast Favoris depuis la fiche utilise le lien commun');
 assert.equal(await js("!!document.getElementById('profil-favoris')"),false,'Reproduction première visite : la cible est absente avant le chargement du Profil');
 assert.equal(await js("getComputedStyle($('toast')).pointerEvents"),'auto','Le toast de confirmation accepte le toucher');
 await send('Emulation.setDeviceMetricsOverride',{width:320,height:568,deviceScaleFactor:2,mobile:true});await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await js("window.__favoriScrollCalls=0;window.__scrollIntoView=Element.prototype.scrollIntoView;Element.prototype.scrollIntoView=function(...a){if(this.id==='profil-favoris')window.__favoriScrollCalls++;return window.__scrollIntoView.apply(this,a)}");
 const toastPoint=await js("(()=>{const r=$('toast').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,visible:getComputedStyle($('toast')).pointerEvents}})()");
 await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:toastPoint.x,y:toastPoint.y}]});await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 const profilAvantDonnees=await js("JSON.stringify({vue:VUE,cible:!!document.getElementById('profil-favoris'),ficheOuverte:FICHE_OUVERTE,ficheVisible:!$('fiche').classList.contains('hidden')})").then(JSON.parse);
 assert.deepEqual(profilAvantDonnees,{vue:'profil',cible:false,ficheOuverte:false,ficheVisible:true},'Le tap navigue vers Profil et ferme la fiche avant le chargement asynchrone');
 await js('await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
 assert.equal(await js('window.__favoriScrollCalls'),0,'Reproduction : le scroll initial ne trouve pas encore la cible Favoris');
 await js('window.__profilResolve();window.fetch=window.__fetchOrig');await sleep(650);
 const targetProfil=await js("(()=>{const x=document.getElementById('profil-favoris');return {vue:VUE,visible:!!x,top:x?.getBoundingClientRect().top,scrollY:scrollY}})()");
 assert.equal(targetProfil.vue,'profil','Le tap sur le toast ouvre le Profil : '+JSON.stringify(targetProfil));assert.ok(targetProfil.visible&&targetProfil.top<100,'Le tap atteint la rangée Favoris après son chargement asynchrone : '+JSON.stringify(targetProfil));
 assert.equal(await js("JSON.stringify({ficheOuverte:FICHE_OUVERTE,ficheMasquee:$('fiche').classList.contains('hidden'),profilVisible:getComputedStyle($('v-profil')).display!=='none'})"),JSON.stringify({ficheOuverte:false,ficheMasquee:true,profilVisible:true}),'Le Profil et sa section Favoris deviennent réellement visibles, sans fiche superposée');
 assert.ok(await js('window.__favoriScrollCalls>0'),'Le scroll vers Favoris doit attendre que le Profil ait créé sa cible');
 // Même navigation depuis une vraie fiche série, cette fois via le click généré par le navigateur tactile.
 await js("onglet('seance');await ouvrirFiche('tv',42)");await sleep(180);
 await js("document.querySelector('[data-t=fi-favori]').click()");await sleep(180);
 assert.equal(await js("$('toast').dataset.t"),'toast-favoris','Une fiche série expose le même toast Favoris');
 const toastSerie=await js("(()=>{const r=$('toast').getBoundingClientRect();return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2})})()").then(JSON.parse);
 await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:toastSerie.x,y:toastSerie.y}]});await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await sleep(500);
 const destinationSerie=await js("(()=>{const c=document.getElementById('profil-favoris');return JSON.stringify({vue:VUE,fiche:FICHE_OUVERTE,masquee:$('fiche').classList.contains('hidden'),top:c?.getBoundingClientRect().top,scrolls:window.__favoriScrollCalls})})()").then(JSON.parse);
 assert.equal(destinationSerie.vue,'profil','Le toast d’une fiche série ouvre Profil : '+JSON.stringify(destinationSerie));assert.equal(destinationSerie.fiche,false);assert.equal(destinationSerie.masquee,true);assert.ok(destinationSerie.top<450&&destinationSerie.scrolls>1,'La section Favoris est amenée dans le viewport : '+JSON.stringify(destinationSerie));
 await js("onglet('seance')");
 await js("onglet('seance');await poll()");
 for(const width of [320,360,390,768,1280]){await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:true});assert.ok(await js("document.documentElement.scrollWidth<=innerWidth&&$('fin-bloc').scrollWidth<=$('fin-bloc').clientWidth"),'Carte de fin sans débordement à '+width+' px')}
 await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});assert.ok(await js("document.documentElement.scrollWidth<=innerWidth&&$('fin-bloc').scrollWidth<=$('fin-bloc').clientWidth"),'Carte de fin sans débordement desktop');
 // Lot 2.6.83 : le carrousel défile dans sa rangée, jamais la page ; mise en page verrouillée aux largeurs iPhone.
 for(const width of [320,375,390,430]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  assert.ok(await js("$('fin-sugg').scrollWidth>=$('fin-sugg').clientWidth"),'La rangée de recommandations est horizontalement défilable à '+width);
  await js("window.scrollTo(60,window.scrollY)");await sleep(60);
  assert.equal(await js("window.scrollX"),0,'La page ne se décale jamais horizontalement à '+width);
  assert.ok(await js("document.documentElement.scrollWidth<=innerWidth&&document.body.scrollWidth<=innerWidth"),'Aucun débordement global à '+width);
 }
 assert.equal(await js("getComputedStyle(document.documentElement).overflowX"),'hidden');
 assert.equal(await js("getComputedStyle(document.documentElement).overscrollBehaviorX"),'none');
 assert.equal(await js("getComputedStyle($('fin-sugg')).overscrollBehaviorX"),'contain');
 assert.match(await js("document.querySelector('meta[name=viewport]').content"),/maximum-scale=1/);
 assert.match(await js("document.querySelector('#fin-sugg .cat-card').getAttribute('aria-label')"),/Suite de la saga/);
 assert.equal(await js("document.querySelector('#fin-sugg .cat-card .suite-lbl').textContent"),'Suite');
 assert.equal(await js("etatAccueil()"),'vide','La fin de séance ne bloque ni lecture ni séance');
 await js("document.querySelector('[data-t=fin-fermer]').click()");await sleep(200);
 assert.equal(lot81.finFermer,1);
 // Lot 2.6.89, test M : fin de saison d'une série, la carte demande si la saison a plu.
 statusForce={en_cours:false,lecture:{actif:false,connecte:true},seance:{nature:'vide',phase:'vide',commandes:false},seance_conservee:null,fin_seance:{t:Date.now()/1000+1,type:'tv',id:108978,saison:1,episode:8,fin_saison:true,mode:'reel',favori:false,meta:{titre:'Reacher',annee:'2022',saison:1,episode:8}},detail:{},lancement:null};await js('await poll()');
 assert.equal(await js("$('fin-q').textContent"),'Avez-vous aimé cet épisode ?');
 assert.equal(await js("$('fin-sub').textContent"),'Séance terminée');
 // ---------- Lot 2.6.93 : cohérence des séances ----------
 const toyA={type:'movie',id:1,titre:'Toy Story 5',annee:'2026',duree:102,affiche:null};
 const seanceActive=(contenu)=>({en_cours:true,controller_running:true,occupation:'seance:1',type:'film',
  lecture:{actif:true,etat:'Playing',connecte:true,total:7200,pos:2052,type:'movie',meta:contenu},seance:{nature:'pilotee',phase:'film',etape:'film',commandes:true},
  contenu,etapes:etapesFilm,detail:{},lancement:null,seance_conservee:null,seances_suspendues:[]});
 const libre={en_cours:false,controller_running:false,lecture:{actif:false,connecte:true},seance:{nature:'vide',phase:'vide',commandes:false},detail:{},lancement:null,seance_conservee:null,seances_suspendues:[]};
 // A : séance active, fiche d'un autre film, Lancer la séance : une seule fenêtre (fiche compacte, options, compte à rebours).
 statusForce=seanceActive(toyA);await js("REG.confirm_delai=10;await poll();await ouvrirFiche('movie',2)");
 derniereStart=null;
 await js("document.querySelector('[data-t=fi-seance]').click()");
 assert.equal(await js("$('m-titre').textContent"),'Séance déjà en cours');
 assert.equal(await js("$('m-texte').textContent"),'La séance actuelle sera mise en suspens et pourra être reprise plus tard.');
 assert.equal(await js("document.querySelector('#m-extra .m-seance-txt b').textContent"),'Toy Story 5');
 assert.equal(await js("document.querySelectorAll('#m-extra [data-t=m-sw]').length"),2,'Options du nouveau média dans la même fenêtre');
 assert.equal(await js("$('m-barbox').classList.contains('hidden')"),false,'Compte à rebours de Lancer la séance');
 assert.equal(await js("$('m-oui').textContent"),'Suspendre et lancer');
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  assert.ok(await js("$('m-oui').scrollWidth<=$('m-oui').clientWidth+1&&$('m-oui').getBoundingClientRect().height<=50&&document.documentElement.scrollWidth<=innerWidth&&$('m-oui').getBoundingClientRect().right<=document.querySelector('#modal .sheet').getBoundingClientRect().right"),'Fenêtre fusionnée lisible à '+width)}
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 writeFileSync('/private/tmp/kc93_fusion.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("document.querySelector('#m-extra [data-k=ba]').click()");
 lot81.libererApresStop=libre;
 await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(1600);
 assert.match(lot81.stopUrl||'',/suspendre=1/,'La séance active part en suspens (workflow de la 2.6.92)');
 assert.match(derniereStart||'',/ba=0&entracte=/,'Le nouveau média démarre directement avec les options choisies');
 assert.equal(await js("$('modal').classList.contains('show')"),false,'Aucune seconde fenêtre');
 // B : sans séance active, Lancer la séance reste la fenêtre habituelle.
 statusForce=libre;await js("await retourFicheTest();await poll();await ouvrirFiche('movie',2)");
 await js("document.querySelector('[data-t=fi-seance]').click()");
 assert.equal(await js("$('m-titre').textContent"),'Lancer la séance');
 assert.equal(await js("$('m-oui').textContent"),'Confirmer');
 assert.equal(await js("document.querySelectorAll('#m-extra .m-seance').length"),0);
 await js("$('m-non').click()");await sleep(250);
 // C : simple lecture manuelle : pas de suspens, « Arrêter et lancer ».
 statusForce={...libre,lecture:{actif:true,etat:'Playing',connecte:true,total:7200,pos:600,type:'movie',meta:toyA},seance:{nature:'detectee',phase:'film',commandes:true}};
 await js("await poll();await ouvrirFiche('movie',2)");
 await js("document.querySelector('[data-t=fi-seance]').click()");
 assert.equal(await js("$('m-titre').textContent"),'Lecture en cours');
 assert.match(await js("$('m-texte').textContent"),/^Une lecture Infuse est en cours\./);
 assert.equal(await js("$('m-oui').textContent"),'Arrêter et lancer');
 assert.doesNotMatch(await js("$('m-texte').textContent"),/suspens/);
 await js("$('m-non').click()");await sleep(250);
 // Lot 2.6.95, cas B et C : YouTube ou Netflix lit, alors qu'une séance KamCiné est seulement en suspens dans la mémoire (le
 // cas matériel) : c'est une lecture, jamais « Séance déjà en cours » ; l'arrêt ne met rien en suspens (suspendre=0).
 const memoire={type:'movie',id:7,origine:'reprise',etat:'ok',confirme:true,suspendue:true,meta:{titre:'Jason Bourne',annee:'2016'},t:5555.5};
 for(const [app,nom,titre] of [['com.google.ios.youtube','YouTube','Une vidéo'],['com.netflix.Netflix','Netflix','']]){
  statusForce={...libre,lecture:{actif:false,connecte:true,ailleurs:{app,app_nom:nom,titre,etat:'Playing',pos:0,total:0,commandes:{}}},seance:{nature:'ailleurs',phase:'lecture',commandes:false},lancement:memoire,seance_conservee:{...memoire,position:600,total:7380,debut:Date.now()/1000-900}};
  await js("await poll();await ouvrirFiche('movie',2)");
  assert.equal(await js('etatAccueil()'),'ailleurs');
  await js("document.querySelector('[data-t=fi-seance]').click()");
  assert.equal(await js("$('m-titre').textContent"),'Lecture en cours',nom+' : pas « Séance déjà en cours »');
  assert.match(await js("$('m-texte').textContent"),new RegExp('^'+nom+' est actuellement en lecture sur l’Apple TV'));
  assert.doesNotMatch(await js("$('m-texte').textContent"),/suspens|séance actuelle/);
  assert.equal(await js("$('m-oui').textContent"),'Arrêter et lancer');
  assert.equal(await js("document.querySelectorAll('#m-extra [data-t=m-sw]').length"),2,'Options et compte à rebours gardés');
  for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
   assert.ok(await js("$('m-oui').scrollWidth<=$('m-oui').clientWidth+1&&$('m-oui').getBoundingClientRect().right<=document.querySelector('#modal .sheet').getBoundingClientRect().right"),nom+' lisible à '+width)}
  assert.equal(await js("document.querySelector('#m-extra .m-seance-txt b').textContent"),titre||nom);
  if(nom==='Netflix'){
   await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
   writeFileSync('/private/tmp/kc95_netflix.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
   lot81.stopUrl='';derniereStart=null;
   await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(600);
   assert.match(lot81.stopUrl,/suspendre=0/,'Aucune séance Netflix mise en suspens');
   assert.match(derniereStart||'',/ignorer_ailleurs=1/);
  }else{await js("$('m-non').click()");await sleep(250)}
 }
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 statusForce=libre;await js('await retourFicheTest();await poll()');
 // D : fiche d'un média en suspens : Reprendre la séance ouvre sa fenêtre d'actions, sans passer par l'accueil.
 const filmB={t:3333.5,type:'movie',id:2,mode:'reel',position:900,total:6000,debut:Date.now()/1000-600,meta:{titre:'Film B',annee:'2020',duree:100,affiche:null}};
 statusForce={...libre,seance_conservee:filmB};
 await js("await poll();await ouvrirFiche('movie',2);renderFiche()");
 assert.match(await js("document.querySelector('[data-t=fi-encours]').textContent"),/Reprendre la séance/);
 await js("document.querySelector('[data-t=fi-encours]').click()");await sleep(100);
 assert.equal(await js("$('m-titre').textContent"),'Séance en suspens');
 assert.equal(await js("FICHE_OUVERTE&&VUE"),'seance','La fiche reste ouverte, aucun détour par l’accueil');
 assert.equal(await js("$('m-oui').textContent"),'Reprendre la séance');
 await js("await new Promise(r=>requestAnimationFrame(r));$('m-oui').click()");await sleep(300);
 assert.match(lot81.reprendre.at(-1)||'',/t=3333\.5/);
 // G : fiche de la séance active : fenêtre d'actions de la séance en cours.
 statusForce=seanceActive({...toyA,id:2,titre:'Film B'});
 await js("await poll();await ouvrirFiche('movie',2);renderFiche()");
 assert.match(await js("document.querySelector('[data-t=fi-encours]').textContent"),/Séance en cours/);
 await js("document.querySelector('[data-t=fi-encours]').click()");await sleep(100);
 assert.equal(await js("$('m-titre').textContent"),'Séance en cours');
 assert.equal(await js("$('m-oui').textContent+'|'+$('m-non').textContent"),'Afficher la séance|Quitter la séance');
 assert.equal(await js("document.querySelector('#m-extra .m-seance-etat').textContent"),'Séance en cours');
 writeFileSync('/private/tmp/kc93_active.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 const stopsAvant=stops;
 await js("$('m-non').click()");
 assert.equal(await js("document.querySelectorAll('#m-extra [data-t=m-sw]').length"),2,'Options de Quitter la séance dans la même fenêtre');
 assert.equal(stops,stopsAvant,'Rien n’est arrêté avant confirmation');
 await js("$('m-x').click()");await sleep(300);
 assert.equal(stops,stopsAvant,'La croix ferme sans action');
 await js("document.querySelector('[data-t=fi-encours]').click()");await sleep(100);
 await js("$('m-non').click();$('m-non').click()");await sleep(400);
 assert.equal(stops,stopsAvant+1,'Quitter la séance confirmé');
 await js('await retourFicheTest()');
 // ---------- Lot 2.6.94 : hiérarchie de l'accueil, rangée, page Séances en suspens, fiche depuis la fenêtre, entracte ----------
 const sus=(t,id,titre,annee,extra)=>({t,type:'movie',id,mode:'reel',position:600,total:6000,debut:Date.now()/1000-900,meta:{titre,annee,duree:100,affiche:null},...(extra||{})});
 const quatre=[sus(4101.5,31,'Avatar','2009'),sus(4102.5,32,'Jason Bourne','2016'),{...sus(4103.5,108978,'Reacher','2022'),type:'tv',saison:1,episode:5},sus(4104.5,34,'Unabomber','2026')];
 statusForce={...seanceActive(toyA),seances_suspendues:quatre};
 await js("HOME_AJOUTES.items="+JSON.stringify([1,2,3,4,5,6].map(i=>({type:'movie',id:900+i,titre:'Ajouté '+i,annee:'2026'})))+";HOME_AJOUTES.t=Date.now();HOME_AJOUTES.sig='';renderHomeAjoutes();onglet('seance');await poll()");
 // A : séance active en haut, séances en suspens juste avant Ajoutés récemment.
 assert.ok(await js("$('sc-bloc').getBoundingClientRect().top<$('sc-int').getBoundingClientRect().top&&$('sc-int').getBoundingClientRect().top<$('home-ajoutes').getBoundingClientRect().top"),'Bloc principal, puis séances en suspens, puis Ajoutés récemment');
 assert.equal(await js("$('sc-int').nextElementSibling.id"),'home-ajoutes');
 // B : trois séances en suspens et trois Ajoutés récemment ; F : aucune barre de progression.
 assert.equal(await js("document.querySelectorAll('#si-liste .si-carte').length"),3);
 assert.equal(await js("document.querySelectorAll('#si-liste .pb').length"),0);
 assert.equal(await js("document.querySelectorAll('#home-ajoutes-row .cat-card').length"),3);
 assert.equal(await js("$('si-badge').parentElement.textContent"),'Séances en suspens›');
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  const m=await js("JSON.stringify({s:[...document.querySelectorAll('#si-liste .si-carte')].map(c=>c.getBoundingClientRect()),a:[...document.querySelectorAll('#home-ajoutes-row .cat-card')].map(c=>c.getBoundingClientRect().width),row:$('si-liste').getBoundingClientRect(),doc:document.documentElement.scrollWidth<=innerWidth,titre:$('sc-int').querySelector('.cat-row').getBoundingClientRect().top-$('sc-int').querySelector('.sec-h').getBoundingClientRect().bottom})").then(JSON.parse);
  assert.ok(m.doc,'Aucun débordement à '+width);
  assert.ok(m.s[2].right<=m.row.right+1,'Trois affiches visibles sans défiler à '+width);
  assert.equal(m.a.length,3,'Trois posters Ajoutés récemment à '+width);assert.ok(m.a[0]>=84,'Posters de Home légèrement agrandis à '+width);
  assert.ok(m.titre>=10,'De la place sous le titre à '+width);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 await js("$('sc-int').scrollIntoView({block:'center'})");
 writeFileSync('/private/tmp/kc94_rangees.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 // C : le chevron ouvre la page de toutes les séances en suspens ; D : une affiche ouvre la même fenêtre d'actions.
 await js("document.querySelector('[data-t=si-tout]').click()");await sleep(200);
 assert.equal(await js('VUE'),'catalogue');
 assert.equal(await js("document.querySelector('.cat-tout-title').textContent"),'Séances en suspens');
 assert.equal(await js("document.querySelectorAll('#cat-tout-grid .si-carte').length"),4,'Toutes les séances en suspens');
 await js("window.scrollTo(0,0)");
 writeFileSync('/private/tmp/kc94_page.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("document.querySelectorAll('#cat-tout-grid .si-carte')[3].click()");await sleep(150);
 assert.equal(await js("$('m-titre').textContent"),'Séance en suspens');
 assert.equal(await js("$('m-oui').textContent+'|'+$('m-non').textContent"),'Reprendre la séance|Quitter la séance');
 // E : la fiche compacte de la fenêtre ouvre la fiche du média, fenêtre refermée ; les boutons restent hors de cette zone.
 assert.equal(await js("!!document.querySelector('#m-extra .m-seance.lien[data-t=m-fiche]')&&!document.querySelector('#m-extra .m-seance #m-oui')"),true);
 await js("document.querySelector('#m-extra .m-seance').click()");await sleep(400);
 assert.equal(await js("$('modal').classList.contains('show')"),false,'Fenêtre refermée');
 assert.equal(await js("FICHE_OUVERTE&&FI&&FI.id"),34,'Fiche d’Unabomber ouverte');
 await js("await retourFicheTest()");await sleep(150);
 await js("document.querySelectorAll('#cat-tout-grid .si-carte')[2].click()");await sleep(150);
 await js("document.querySelector('#m-extra .m-seance').click()");await sleep(400);
 assert.equal(await js("FI&&FI.type+'-'+FI.id+'-'+FI.saison+'-'+FI.episode"),'tv-108978-1-5','Fiche de l’épisode d’une série');
 await js("await retourFicheTest()");await sleep(150);
 await js("document.querySelector('[data-t=cat-all-back]').click()");await sleep(200);
 assert.equal(await js('VUE'),'seance','Retour à l’accueil depuis la page');
 // H et I : l'option Annuler l'entracte prévue suit l'état réel de la séance, pas le réglage global.
 statusForce={...seanceActive(toyA),options:{ba:true,entracte:true},detail:{entracte:{etat:'attente',prevu_s:3600,prevu_pct:50}}};
 await js("REG.entracte_actif=true;await poll()");
 await js("$('btn-entracte').click()");
 assert.equal(await js("document.querySelectorAll('#m-extra [data-k=annuler]').length"),1,'Entracte prévue : option présente');
 await js("$('m-non').click()");await sleep(250);
 statusForce={...seanceActive(toyA),options:{ba:true,entracte:false},detail:{entracte:{etat:'attente',prevu_s:null,prevu_pct:null}}};
 await js("REG.entracte_actif=true;await poll()");
 await js("$('btn-entracte').click()");
 assert.equal(await js("$('m-titre').textContent"),"Envoyer l'entracte");
 assert.equal(await js("document.querySelectorAll('#m-extra [data-k=annuler]').length"),0,'Séance sans entracte : rien à annuler');
 assert.equal(await js("$('m-barbox').classList.contains('hidden')"),false,'Le compte à rebours reste');
 await js("$('m-non').click()");await sleep(250);
 // ---------- Point 68 (2.6.96) : sélecteur roulette de programmation ----------
 statusForce=libre;
 lot81.planHeure={maintenant_local:'2026-09-28T20:05',plus_tot_local:'2026-09-28T20:07',plus_tot:Date.now()/1000+120};
 planSeances=[];lot81.plan=[];
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 await js("await pollPlan();await poll();await ouvrirFiche('movie',2);FI.meta.titre='Toy Story 5';ouvrirProg()");await sleep(300);
 // A : nouveau sélecteur, trois listes accessibles ; B : minutes 00, 15, 30, 45 et heures 00 à 23.
 assert.equal(await js("$('m-titre').textContent"),'Programmer une séance');
 assert.equal(await js("document.querySelectorAll('#m-roue [role=listbox]').length"),3);
 assert.equal(await js("document.querySelectorAll('input[type=datetime-local]').length"),0,'Plus de champ de saisie de date');
 assert.deepEqual(JSON.parse(await js("JSON.stringify([...document.querySelectorAll('[data-col=m].roue-col .roue-opt')].map(o=>o.dataset.v))")),['00','15','30','45']);
 assert.equal(await js("document.querySelectorAll('[data-col=h].roue-col .roue-opt').length"),24);
 // C : 20:07 donne 20:15, récapitulatif lisible en français, valeur réelle du formulaire.
 assert.equal(await js("$('m-dt').value"),'2026-09-28T20:15');
 assert.equal(await js("$('m-recap').firstChild.textContent"),'Lundi 28 septembre · 20:15');
 assert.equal(await js("document.querySelector('[data-col=j].roue-col .roue-opt').textContent"),'Aujourd’hui');
 assert.equal(await js("document.querySelector('[data-col=j].roue-col').getAttribute('aria-activedescendant')"),'roue-j-2026-09-28');
 // D : jamais dans le passé : heures et minutes passées désactivées, une valeur passée est ramenée au plus tôt.
 assert.equal(await js("document.querySelector('#roue-h-19').getAttribute('aria-disabled')+'|'+document.querySelector('#roue-m-00').getAttribute('aria-disabled')"),'true|true');
 await js("roueChoisir('h','18')");
 assert.equal(await js("$('m-dt').value"),'2026-09-28T20:15','Heure passée refusée');
 await js("roueChoisir('h','21')");
 assert.equal(await js("document.querySelector('#roue-m-00').getAttribute('aria-disabled')"),'false','21:00 redevient possible');
 await js("roueChoisir('m','00')");
 assert.equal(await js("$('m-dt').value"),'2026-09-28T21:00');
 // Défilement réel d'une colonne : l'option centrée devient la valeur.
 await js("{const col=document.querySelector('[data-col=h].roue-col'),o=$('roue-h-22');col.scrollTop=o.offsetTop-(col.clientHeight-o.offsetHeight)/2;col.dispatchEvent(new Event('scroll'))}");await sleep(600);
 assert.equal(await js("$('m-dt').value"),'2026-09-28T22:00','Défilement tactile pris en compte');
 // Clavier : flèche bas sur les minutes.
 await js("document.querySelector('[data-col=m].roue-col').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))");
 assert.equal(await js("$('m-dt').value"),'2026-09-28T22:15','Utilisable au clavier');
 // E : passage de fin de mois dans la liste des jours.
 await js("roueChoisir('j','2026-10-01')");
 assert.equal(await js("$('m-recap').firstChild.textContent"),'Jeudi 1 octobre · 22:15');
 assert.equal(await js("JSON.stringify(joursRoue('2026-09-29',3))"),JSON.stringify(['2026-09-29','2026-09-30','2026-10-01']));
 // F : décembre puis janvier, année affichée ; G : années bissextiles.
 assert.equal(await js("JSON.stringify(joursRoue('2026-12-30',3))"),JSON.stringify(['2026-12-30','2026-12-31','2027-01-01']));
 assert.equal(await js("libJourRoue('2027-01-01','2026-09-28')"),'Ven. 1 janv. 2027');
 assert.equal(await js("quartSuivant('2026-12-31T23:50')"),'2027-01-01T00:00');
 assert.equal(await js("JSON.stringify(joursRoue('2028-02-28',2))"),JSON.stringify(['2028-02-28','2028-02-29']));
 assert.equal(await js("JSON.stringify(joursRoue('2027-02-28',2))"),JSON.stringify(['2027-02-28','2027-03-01']));
 assert.equal(await js("libDateLongue('2028-02-29T21:00','2028-01-10')"),'Mardi 29 février · 21:00');
 // H : options du film conservées ; I : le réglage « Je suis là » reste global, rien ne change dans la modal.
 assert.equal(await js("document.querySelectorAll('#m-extra [data-t=m-sw]').length"),2);
 assert.match(await js("$('m-texte').textContent"),/Mode réel/);
 assert.equal(await js("$('m-rappel-avec').checked&&$('m-rappel-delais').hidden"),false,'Rappel actif par défaut avec délai visible');
 assert.equal(await js("$('m-rappel-delai').value"),'15');
 await js("document.querySelector('[data-t=rappel-choice][data-v=sans]').click()");
 assert.equal(await js("$('m-rappel-delais').hidden"),true,'Sans rappel masque le délai');
 await js("document.querySelector('[data-t=rappel-choice][data-v=avec]').click();$('m-rappel-delai').value='60'");
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  const m=await js("JSON.stringify({doc:document.documentElement.scrollWidth<=innerWidth,sheet:document.querySelector('#modal .sheet').getBoundingClientRect().height,coupe:[...document.querySelectorAll('.roue-opt')].slice(0,40).filter(o=>o.scrollWidth>o.clientWidth+1).map(o=>o.textContent),ligne:$('roue-h-20').getBoundingClientRect().height,oui:$('m-oui').getBoundingClientRect().bottom<=innerHeight+1})").then(JSON.parse);
  assert.ok(m.doc,'Pas de débordement à '+width);
  assert.deepEqual(m.coupe,[],'Aucun libellé coupé à '+width);
  assert.ok(m.ligne>=34,'Options assez grandes au doigt à '+width);
  assert.ok(m.sheet<=844-32,'Modal pas démesurée à '+width);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 await js("roueChoisir('j','2026-09-28');roueChoisir('h','20');roueChoisir('m','30')");await sleep(400);
 writeFileSync('/private/tmp/kc96_roue.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 // J : enregistrement au format actuel du service, options comprises, puis relecture sur l'accueil.
 await js("document.querySelector('#m-extra [data-k=ent]').click()");
 await js("$('m-oui').click()");await sleep(500);
 const ajout=lot81.plan.find(x=>x.p==='/planning/ajouter');
 assert.equal(ajout.c.quand,'2026-09-28T20:30');
 assert.deepEqual([ajout.c.bandes_annonces,ajout.c.entracte],[true,false]);
 assert.equal(ajout.c.rappel_actif,true);assert.equal(ajout.c.rappel_min,60,'Le délai choisi est transmis au planificateur');
 await js("await retourFicheTest();await pollPlan();onglet('seance')");
 assert.match(await js("$('plan-proches-list').textContent+$('plan-list').textContent"),/Toy Story 5/);
 // K : reprogrammation d'une séance existante avec le même sélecteur, placé sur son heure.
 await js("ouvrirModifier('n1')");await sleep(300);
 assert.equal(await js("$('m-dt').value"),'2026-09-28T20:30','Sélecteur placé sur l’heure de la séance');
 await js("roueChoisir('h','21')");
 await js("$('m-oui').click()");await sleep(400);
 const modif=lot81.plan.find(x=>x.p==='/planning/modifier');
 assert.deepEqual(modif.c,{id:'n1',quand:'2026-09-28T21:30',rappel_actif:true,rappel_min:60});
 // ---------- Point 68, finitions (2.6.97) ----------
 // A et B : la roulette reste montée, la zone dynamique garde sa hauteur pendant la vérification (pas de saut ni d'éclair).
 await js("ouvrirModifier('n1')");await sleep(400);
 await js("window.__roue=$('m-roue');window.__info=$('m-plan-info')");
 lot81.verifDelai=350;
 const mesure="JSON.stringify({h:document.querySelector('#modal .sheet').getBoundingClientRect().height,i:$('m-plan-info').getBoundingClientRect().height,t:$('m-plan-info').textContent,memeRoue:$('m-roue')===window.__roue&&$('m-plan-info')===window.__info,busy:$('m-plan-info').getAttribute('aria-busy')})";
 const avant=await js(mesure).then(JSON.parse);
 assert.match(avant.t,/Créneau libre/);
 for(const [c,v] of [['h','22'],['m','45'],['j','2026-09-29'],['h','21']]){
  await js("roueChoisir('"+c+"','"+v+"',true)");await sleep(80);
  const pendant=await js(mesure).then(JSON.parse);
  assert.equal(pendant.memeRoue,true,'Roulette et zone de message jamais reconstruites ('+c+')');
  assert.equal(pendant.busy,'true','Vérification en cours signalée');
  assert.match(pendant.t,/Créneau libre/,'Ancien message gardé pendant la vérification ('+c+')');
  assert.ok(Math.abs(pendant.h-avant.h)<1&&Math.abs(pendant.i-avant.i)<1,'Hauteur stable pendant la vérification ('+c+') : '+JSON.stringify([avant.h,pendant.h,avant.i,pendant.i]));
  await sleep(420);
  const apres=await js(mesure).then(JSON.parse);
  assert.equal(apres.busy,null);
  assert.ok(Math.abs(apres.h-avant.h)<1,'Hauteur stable après la vérification ('+c+')');
 }
 lot81.verifDelai=0;
 // C : espacements aux largeurs iPhone.
 for(const width of [320,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});
  const e=await js("JSON.stringify({recap:$('m-recap').getBoundingClientRect().top-$('m-roue').getBoundingClientRect().bottom,info:$('m-plan-info').getBoundingClientRect().top-$('m-recap').getBoundingClientRect().bottom,doc:document.documentElement.scrollWidth<=innerWidth})").then(JSON.parse);
  assert.ok(e.recap>=14&&e.recap<=24,'Marge au dessus du récapitulatif à '+width+' : '+e.recap);
  assert.ok(e.info>=8&&e.info<=18,'Marge entre récapitulatif et message à '+width+' : '+e.info);
  assert.ok(e.doc,'Pas de débordement à '+width)}
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 // F et H : Changer l'heure en vert, Annuler neutre, action Annuler la séance rouge dans la même interface.
 const vert='rgb(52, 195, 143)';
 assert.equal(await js("getComputedStyle($('m-oui')).backgroundColor"),vert,'Changer l’heure : validation verte');
 const neutre=await js("getComputedStyle($('m-non')).backgroundColor");
 assert.ok(neutre!==vert&&neutre!=='rgb(245, 9, 31)','Annuler reste neutre');
 assert.equal(await js("document.querySelector('.m-plan-annuler').textContent"),'Annuler la séance');
 assert.equal(await js("getComputedStyle(document.querySelector('.m-plan-annuler')).color"),'rgb(245, 9, 31)');
 // I et J : la fiche ouvre exactement la même interface, avec les mêmes données, que Modifier sur l'accueil.
 await js("$('m-non').click()");await sleep(250);
 await js("await retourFicheTest();onglet('seance');await pollPlan()");
 await js("document.querySelector('[data-t=plan-modifier][data-p=n1]').click()");await sleep(400);
 const depuisAccueil=await js("JSON.stringify({t:$('m-titre').textContent,x:$('m-texte').textContent,q:$('m-dt').value,oui:$('m-oui').textContent,bas:document.querySelector('.m-plan-annuler').dataset.p})");
 await js("$('m-non').click()");await sleep(250);
 await js("await ouvrirFiche('movie',2)");await sleep(200);
 assert.equal(await js("!!document.querySelector('[data-t=fi-prog-liste]')"),true,'Badge Séance programmée sur la fiche');
 await js("document.querySelector('[data-t=fi-prog-liste]').click()");await sleep(400);
 assert.equal(await js("JSON.stringify({t:$('m-titre').textContent,x:$('m-texte').textContent,q:$('m-dt').value,oui:$('m-oui').textContent,bas:document.querySelector('.m-plan-annuler').dataset.p})"),depuisAccueil,'Même interface et mêmes données depuis la fiche');
 assert.equal(await js("$('m-titre').textContent"),'Changer l’heure de la séance'.replace('’',"'"));
 assert.equal(await js("document.querySelectorAll('.choix-liste').length"),0,'Plus de modal Séance programmée spécifique');
 // G : l'action destructive reste rouge (confirmation d'annulation).
 await js("document.querySelector('.m-plan-annuler').click()");await sleep(250);
 assert.equal(await js("$('m-titre').textContent"),'Annuler la séance ?');
 assert.equal(await js("getComputedStyle($('m-oui')).backgroundColor"),'rgb(245, 9, 31)','Annuler la séance : rouge');
 await js("$('m-non').click()");await sleep(250);
 await js("await retourFicheTest()");await sleep(150);
 // D et E : options positives en vert, désactivées neutres ; Programmer en vert.
 await js("await ouvrirFiche('movie',2);ouvrirProg()");await sleep(300);
 assert.equal(await js("getComputedStyle(document.querySelector('#m-extra [data-k=ba]')).backgroundColor"),vert,'Bandes annonces activées : vert');
 assert.equal(await js("getComputedStyle(document.querySelector('#m-extra [data-k=ent]')).backgroundColor"),vert,'Entracte activée : vert');
 await js("document.querySelector('#m-extra [data-k=ent]').click()");await sleep(350);
 assert.notEqual(await js("getComputedStyle(document.querySelector('#m-extra [data-k=ent]')).backgroundColor"),vert,'Désactivée : neutre');
 assert.equal(await js("$('m-oui').textContent+'|'+getComputedStyle($('m-oui')).backgroundColor"),'Programmer|'+vert);
 writeFileSync('/private/tmp/kc97_programmer.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await js("$('m-non').click()");await sleep(250);await js("await retourFicheTest()");await sleep(150);
 // « Prendre le créneau libre » : une heure quelconque du service est placée au quart d'heure suivant.
 await js("ouvrirModifier('n1')");await sleep(300);
 await js("const b=document.createElement('button');b.dataset.t='plan-libre';b.dataset.q='2026-09-28T22:47';$('m-plan-info').appendChild(b);b.click()");await sleep(200);
 assert.equal(await js("$('m-dt').value"),'2026-09-28T23:00');
 await js("$('m-non').click()");await sleep(250);
 planSeances=[];lot81.planHeure=null;await js('await pollPlan()');
 // Pastille Téléchargement d'une fiche : ouvre la page Téléchargements de KamCiné, pas Radarr.
 statusForce=null;await js("await poll();await ouvrirFiche('movie',41)");
 await js("FI.statut={hd:{etat:'telechargement',progres:71,source:'transmission'},uhd:{etat:'recherche',source:'radarr'},global:'telechargement',sources:{}};renderFiche()");
 assert.equal(await js("document.querySelectorAll('[data-t=fi-dl-page]').length"),1,'Seule la qualité en téléchargement renvoie vers la page');
 assert.match(await js("document.querySelector('[data-t=fi-dl-page]').textContent"),/71 %/);
 await js("document.querySelector('[data-t=fi-dl-page]').click()");await sleep(200);
 assert.equal(await js('VUE'),'telechargements');
 assert.equal(await js('FICHE_OUVERTE'),false);
 const carteBandeAnnonce=await js("(()=>{const n=document.createElement('div');n.innerHTML=baHTML({film:{type:'movie',id:18,titre:'Bande annonce',annee:'2026',affiche:'https://image.tmdb.org/t/p/w185/test.jpg'}});document.body.appendChild(n);const b=n.querySelector('.ba-card'),r=getComputedStyle(b.querySelector('.ba-ph')).aspectRatio;n.remove();return JSON.stringify({bouton:!!b,chevron:!!b?.querySelector('.ba-chevron'),liens:b?.querySelectorAll('a').length||0,texte:b?.textContent||'',ratio:r})})()").then(JSON.parse);
 assert.equal(carteBandeAnnonce.bouton,true);assert.equal(carteBandeAnnonce.chevron,true);assert.equal(carteBandeAnnonce.liens,0);assert.match(carteBandeAnnonce.texte,/Bande annonce en cours/);assert.match(carteBandeAnnonce.ratio,/^1\.45/);
 const badges=await js("(()=>{const mk=(type,id,hd,uhd)=>{const w=document.createElement('div');w.innerHTML=carteHTML({type,id,titre:'Test',annee:'2024'});const c=w.firstElementChild;ETATS[type+'-'+id]={fiable:false,hd_poster:hd,uhd_poster:uhd};appliquerEtats([c]);return c};const both=mk('movie',842,'available','available'),mixed=mk('movie',844,'available','requested'),series=mk('tv',847,'available','downloading'),upcoming=mk('movie',845,'upcoming','upcoming'),fiche=document.createElement('span');fiche.className='stb dispo';fiche.textContent='Disponible';return JSON.stringify({both:[...both.querySelectorAll('.qual-i span')].map(x=>[x.textContent,...x.classList]),mixed:[...mixed.querySelectorAll('.qual-i span')].map(x=>[x.textContent,...x.classList]),series:[...series.querySelectorAll('.qual-i span')].map(x=>[x.textContent,...x.classList]),upcoming:[...upcoming.querySelectorAll('.qual-i span')].map(x=>[x.textContent,...x.classList]),greenPoster:getComputedStyle(both.querySelector('.poster-quality.dispo')).color,greenFiche:getComputedStyle(fiche).color,triangle:(()=>{const vu=document.createElement('div');vu.innerHTML=carteHTML({type:'movie',id:843,titre:'Vu',annee:'2024'});VUS['movie-843']=true;appliquerVus([vu.firstElementChild]);const unseen=document.createElement('div');unseen.innerHTML=carteHTML({type:'movie',id:846,titre:'Non vu',annee:'2024'});VUS['movie-846']=false;appliquerVus([unseen.firstElementChild]);return [!!unseen.querySelector('.vu-i'),!!unseen.querySelector('.vu-i svg'),!!vu.querySelector('.vu-i')]})(),other:!!both.querySelector('.mk')||!!both.querySelector('.dlb')})})()").then(JSON.parse);
 assert.equal(badges.both.length,1);assert.equal(badges.both[0][0],'HD et 4K');assert.ok(badges.both[0].includes('dispo'));assert.equal(badges.mixed.length,2);assert.ok(badges.mixed[0].includes('dispo'));assert.ok(badges.mixed[1].includes('requested'));assert.deepEqual(badges.series,[['HD','poster-quality','dispo'],['4K','poster-quality','cours']]);assert.deepEqual(badges.upcoming,[['HD et 4K','poster-quality','venir']]);assert.deepEqual(badges.triangle,[true,false,false]);assert.equal(badges.greenPoster,badges.greenFiche,'Les verts Disponible des badges poster et fiche partagent la couleur centrale');assert.equal(badges.other,false);
 assert.match(await js("lgHTML('rt','72')"),/src="\/logos\/rt_fresh\.svg"/,'La pastille Rotten Tomatoes utilise l’asset privé officiel historique');assert.equal(await js("lgHTML('pub')"),'','La source Rotten Tomatoes public ne duplique plus le logo/source');
 const notesLayout=await js("(()=>{const e=document.createElement('div');e.className='pg-notes';e.innerHTML='<a class=note>'+lgHTML('imdb')+'<b>8,2</b></a><a class=note>'+lgHTML('rt','72')+'<b>72 %</b></a>';document.body.appendChild(e);const r=[...e.querySelectorAll('.note')].map(n=>({gap:getComputedStyle(n).gap,source:n.querySelector('.lg').getBoundingClientRect(),score:n.querySelector('b').getBoundingClientRect()}));e.remove();return JSON.stringify(r.map(x=>({gap:x.gap,sourceWidth:x.source.width,scoreLeft:x.score.left,sourceRight:x.source.right,sourceTop:x.source.top,scoreTop:x.score.top})))})()").then(JSON.parse);
 assert.equal(notesLayout.length,2);assert.ok(notesLayout.every(x=>x.gap==='5px'&&x.scoreLeft>=x.sourceRight+5&&Math.abs(x.sourceTop-x.scoreTop)<6),'Sources et notes séparées, alignées, sans chevauchement');
 assert.equal(await js("typeof streamingHTML"),'undefined','Où regarder a été retiré entièrement du frontend');
 assert.match(await js("lgHTML('imdb')"),/imdb\.svg/);assert.match(await js("lgHTML('mc')"),/metacritic\.svg/,'Les logos IMDb et Metacritic utilisent les assets locaux historiques');
 assert.equal(await js("(()=>{const s=baCorps({dispo:false,message:'Trouvée seulement sur une autre chaîne',detail:'diagnostic technique',raison:'autre_chaine'},{titre:'Test'},false,false);return !s.includes('autre chaîne')&&!s.includes('diagnostic technique')&&s.includes('Aucune bande-annonce exploitable')})()"),true,'Le diagnostic technique de sélection reste invisible dans la modale');
 assert.equal(await js("['Radarr','Sonarr','Overseerr','Sofa Time','Infuse'].every(n=>serviceBadge(n).includes('data-service-logo'))"),true,'Accès rapides et Ouvrir dans utilisent les assets optionnels locaux');assert.equal(await js("serviceBadge('Transmission').includes('<svg')"),true,'Un service sans asset historique conserve son fallback générique');
 collectionPreviewDelay=250;
 await js("window.__oldFi=FI;OVOK=true;const f={collection:{id:999,ids:[301,302,303,304],demandeBusy:false}};FI=f;window.__collectionTest=f;window.__collectionStart=performance.now();demanderCollectionFiche(f)");await sleep(35);
 const modalInstant=await js("JSON.stringify({visible:!$('modal').classList.contains('hidden'),elapsed:performance.now()-window.__collectionStart,button:$('m-oui').classList.contains('hidden')})").then(JSON.parse);
 assert.equal(modalInstant.visible,true,'La modale Demander la saga apparaît sans attendre le réseau');assert.ok(modalInstant.elapsed<120,'La modale s’ouvre immédiatement');assert.equal(modalInstant.button,true,'Aucune confirmation possible avant la résolution du statut');
 await sleep(300);assert.match(await js("$('m-titre').textContent"),/Demander la saga/);assert.equal(await js("document.querySelectorAll('#m-extra [data-t=fi-collection-quality]').length"),3,'Les choix se remplissent dans la modale déjà ouverte');
 await js("$('m-non').click();FI=window.__oldFi;OVOK=false");collectionPreviewDelay=0;
 collectionEtatsFailures=1;collectionEtatsRequests=0;
 await js("FI=window.__collectionTest;chargerCollectionFiche(FI)");await sleep(60);
 assert.equal(await js("window.__collectionTest.collection.etatsCharge||false"),false,'Une collection issue du cache attend la confirmation réseau de ses index');
 await sleep(2050);
 assert.equal(await js("window.__collectionTest.collection.etatsCharge"),true,'Les badges de saga sont complétés après le rafraîchissement réseau asynchrone');
 assert.equal(collectionEtatsRequests,2,'Le retry reste un seul appel groupé pour toute la collection');
 await js("FI=window.__oldFi");collectionEtatsFailures=0;
 const filtre=await js("(()=>{CAT.tout={kind:'tendances',type:'all',filtres:{type:'tout',dispo:'tout',genre:'',annee:'',tri:'popularite'},genres:[]};return filtresCatalogueToutHTML(CAT.tout)})()");
 assert.match(filtre,/Films et séries/);assert.doesNotMatch(filtre,/Disponibles|Absents/);assert.match(filtre,/Tous les genres/);
 const rappel=await js("controleRappelHTML('m',{actif:true,min:30})");assert.match(rappel,/class=\"att-segment\"/);assert.match(rappel,/Avec rappel/);assert.match(rappel,/30 min avant/);
 const retourBa=await js("(()=>{VUE='seance';FICHE_OUVERTE=false;PILE=[];ST={...(ST||{}),bande_annonce:{type:'movie',id:18,titre:'Test BA',affiche:null}};const b=document.createElement('button');b.dataset.t='ba-fiche-home';document.body.appendChild(b);b.click();const ouverte=FICHE_OUVERTE,vue=VUE;clearNavigationStack();b.remove();return JSON.stringify({ouverte,vue,retour:VUE,fermee:!FICHE_OUVERTE})})()").then(JSON.parse);
 assert.deepEqual(retourBa,{ouverte:true,vue:'seance',retour:'seance',fermee:true},'La fiche issue du scénario revient au scénario');
 // Catalogue principal : onglet, type, disponibilité, genre, année, tri, changement rapide, scroll et retour de fiche.
 const nbRequetesCatalogueAvant=catalogueRequests.length;
 await js("CAT.tout=null;CAT.init=false;CAT.filtre='tout';CAT.dispoMode='tout';CAT.vuMode='tous';CAT.q='';CAT.pf={genre:'',annee:'',tri:'popularite'};TMDBOK=true;REG.badges_vu_actif=true;onglet('catalogue')");await sleep(220);
 assert.equal(await js("$('catalogue-body').querySelector('h1')?.textContent"),'Catalogue','Ouverture du Catalogue principal');
 await js("document.querySelector('#fchips [data-t=filtre][data-v=movie]').click()");await sleep(120);
 assert.equal(await js("CAT.filtre"),'movie');assert.equal(await js("!!document.querySelector('#grille-tous')"),true,'L’onglet Films charge la grille principale');
 await js("document.querySelector('#fchips [data-t=filtre][data-v=tout]').click()");await sleep(100);
 assert.equal(await js("!!document.querySelector('#cat-out .sec-slot:not(.genre-slot)')"),true,'Découvrir reprend ses rangées après une vue Films initiale');
 await js("document.querySelector('#fchips [data-t=filtre][data-v=movie]').click()");await sleep(100);
 await js("document.querySelector('#explore [data-t=vue][data-v=genres]')?.click()");await sleep(100);
 assert.equal(await js("!!document.querySelector('#cat-out .genre-slot')"),true,'La vue par genre utilise ses rangées distinctes');
 await js("document.querySelector('#explore [data-t=vue][data-v=tous]')?.click()");await sleep(100);
 assert.equal(await js("!!document.querySelector('#grille-tous')"),true,'Le retour à Tous les films reconstruit la grille du Catalogue');
 await js("document.querySelector('#fchips [data-t=cat-filters]').click()");
 assert.equal(await js("document.querySelectorAll('#fchips [data-t=cat-vus-mode]').length"),3,'Le filtre Vu/Non vu expose trois états explicites');
 const trameTraktReelle=await js("(()=>{const g=$('grille-tous'),a=document.createElement('div');a.innerHTML=carteHTML({type:'movie',id:533535,titre:'Fixture Trakt A'});const x=a.firstElementChild,b=document.createElement('div');b.innerHTML=carteHTML({type:'movie',id:122,titre:'Fixture Trakt B'});const y=b.firstElementChild;VUS={};VUS_READY={};VUS['movie-533535']=true;VUS_READY['movie-533535']=true;VUS['movie-122']=true;VUS_READY['movie-122']=true;VUS['movie-101']=false;VUS_READY['movie-101']=true;VUS['movie-102']=false;VUS_READY['movie-102']=true;CAT.filtre='movie';CAT.dispoMode='tout';CAT.vuMode='vus';appliquerFiltre([x,y]);const reel=[x,y].filter(c=>!c.classList.contains('filter-out')).map(c=>c.dataset.id);VUS_READY['movie-101']=false;CAT.vuMode='vus';appliquerFiltre([...$('grille-tous').querySelectorAll('.cat-card')]);const inconnuVisible=[...$('grille-tous').querySelectorAll('.cat-card')].some(c=>c.dataset.id==='101'&&!c.classList.contains('filter-out'));return JSON.stringify({reel,inconnuVisible,pending:$('cat-watch-pending')?.textContent||''})})()").then(JSON.parse);
 assert.deepEqual(trameTraktReelle.reel,['533535','122'],'La forme réelle des IDs TMDB Trakt sélectionne les fiches correspondantes');
 assert.equal(trameTraktReelle.inconnuVisible,false,'Un état de visionnage inconnu ne se présente jamais comme un résultat Vus');
 assert.match(trameTraktReelle.pending,/Trakt/,'Les éléments inconnus sont signalés pendant la résolution');
 const propagationVu=await js("(()=>{const s=document.createElement('section');s.className='sec-slot genre-slot';s.dataset.genreLoaded='true';const row=document.createElement('div');row.className='cat-row';row.dataset.genre='27';row.dataset.gtype='movie';row.innerHTML=carteHTML({type:'movie',id:1083381,titre:'Backrooms',genres:[27]});const c=row.firstElementChild;s.append(row);$('cat-out').append(s);ETATS['movie-1083381']={dispo:'absent'};VUS['movie-1083381']=false;VUS_READY['movie-1083381']=true;CAT.filtre='movie';CAT.dispoMode='tout';CAT.vuMode='vus';appliquerFiltre([c],false);const avant=c.classList.contains('filter-out');apresVu({type:'movie',id:1083381},true);const resultat={avant,vu:VUS['movie-1083381'],pret:VUS_READY['movie-1083381'],visible:!c.classList.contains('filter-out'),rangeeVisible:!s.classList.contains('hidden'),toastPosition:getComputedStyle($('cat-watch-pending')).position};s.remove();return JSON.stringify(resultat)})()").then(JSON.parse);
 assert.deepEqual(propagationVu,{avant:true,vu:true,pret:true,visible:true,rangeeVisible:true,toastPosition:'fixed'},'Le vrai identifiant TMDB réintègre immédiatement la rangée Horreur dans Vus sans confondre Vu et Disponible');
 await js("VUS['movie-101']=true;VUS_READY['movie-101']=true;VUS['movie-102']=false;VUS_READY['movie-102']=true;CAT.vuMode='tous';appliquerFiltre()");
 await js("VUS['movie-101']=true;VUS_READY['movie-101']=true;VUS['movie-102']=false;VUS_READY['movie-102']=true;CAT.vuMode='tous';appliquerFiltre();window.__seenMutations=0;window.__seenObserver=new MutationObserver(rs=>window.__seenMutations+=rs.filter(r=>r.type==='childList').length);window.__seenObserver.observe($('grille-tous'),{childList:true,subtree:true})");
 for(const [mode,attendu] of [['non-vus',['102']],['vus',['101']],['tous',['101','102']]]){
  await js("document.querySelector('#fchips [data-t=cat-vus-mode][data-v="+mode+"]').click()");await sleep(30);
  const visibles=await js("JSON.stringify([...$('grille-tous').querySelectorAll('.cat-card')].filter(c=>!c.classList.contains('filter-out')).map(c=>c.dataset.id).sort())").then(JSON.parse);
  assert.deepEqual(visibles,attendu,'Films : état de visionnage '+mode);
 }
 assert.equal(await js('window.__seenMutations'),0,'Les filtres Vu/Non vu des films ne reconstruisent aucune grille');await js("window.__seenObserver.disconnect();document.querySelector('#fchips [data-t=filtre][data-v=tv]').click()");await sleep(160);
 await js("VUS['tv-201']=true;VUS_READY['tv-201']=true;VUS['tv-202']=false;VUS_READY['tv-202']=true;CAT.vuMode='tous';appliquerFiltre();window.__seenMutations=0;window.__seenObserver=new MutationObserver(rs=>window.__seenMutations+=rs.filter(r=>r.type==='childList').length);window.__seenObserver.observe($('grille-tous'),{childList:true,subtree:true})");
 for(const [mode,attendu] of [['non-vus',['202']],['vus',['201']],['tous',['201','202']]]){
  await js("document.querySelector('#fchips [data-t=cat-vus-mode][data-v="+mode+"]').click()");await sleep(30);
  const visibles=await js("JSON.stringify([...$('grille-tous').querySelectorAll('.cat-card')].filter(c=>!c.classList.contains('filter-out')).map(c=>c.dataset.id).sort())").then(JSON.parse);
  assert.deepEqual(visibles,attendu,'Séries : état de visionnage '+mode);
 }
 assert.equal(await js('window.__seenMutations'),0,'Les filtres Vu/Non vu des séries ne reconstruisent aucune grille');await js("window.__seenObserver.disconnect();CAT.vuMode='tous';synchroniserCommandesCatalogue();document.querySelector('#fchips [data-t=filtre][data-v=movie]').click()");await sleep(150);
 await js("CAT.dispoMode='disponibles';CAT.pf.genre='28';CAT.pf.annee='2024';VUS['movie-101']=true;VUS_READY['movie-101']=true;VUS['movie-102']=false;VUS_READY['movie-102']=true;CAT.vuMode='vus';appliquerFiltre();");
 assert.deepEqual(await js("JSON.stringify([...$('grille-tous').querySelectorAll('.cat-card')].filter(c=>!c.classList.contains('filter-out')).map(c=>c.dataset.id))").then(JSON.parse),['101'],'Intersection Films + disponible + Action + 2024 + vus');
 const taillesFiltres=await js("JSON.stringify({dispo:document.querySelector('#cat-dispo .fchip').getBoundingClientRect().height,vu:document.querySelector('[data-t=cat-vus-mode]').getBoundingClientRect().height,nonVus:document.querySelector('[data-t=cat-vus-mode][data-v=non-vus]').getAttribute('aria-label'),vus:document.querySelector('[data-t=cat-vus-mode][data-v=vus]').getAttribute('aria-label')})").then(JSON.parse);
 assert.equal(taillesFiltres.vu,taillesFiltres.dispo,'Pilules Visionnage et Disponibilité ont la même hauteur');assert.match(taillesFiltres.nonVus,/Non vus/);assert.match(taillesFiltres.vus,/Vus/);
 await js("CAT.vuMode='non-vus';appliquerFiltre();CAT.vuMode='vus';CAT.dispoMode='absents';appliquerFiltre();CAT.dispoMode='disponibles';CAT.vuMode='tous';appliquerFiltre();CAT.dispoMode='tout';CAT.vuMode='tous';appliquerFiltre()");
 const courseVus=await js("JSON.stringify(await (async()=>{const original=api;let fins=[];api=(url)=>new Promise(resolve=>fins.push({url,resolve}));const k='movie-99001',k2='movie-99002';const ancien=chargerVus([],[k]),recent=chargerVus([],[k,k2]);await new Promise(r=>setTimeout(r,0));fins[1].resolve({ok:true,j:{vus:{[k]:false,[k2]:false},pret:true}});await new Promise(r=>setTimeout(r,0));fins[0].resolve({ok:true,j:{vus:{[k]:true},pret:true}});await Promise.all([ancien,recent]);api=original;return {vu:VUS[k],pret:VUS_READY[k]}})())").then(JSON.parse);
 assert.deepEqual(courseVus,{vu:false,pret:true},'Une réponse Vus tardive ne peut pas écraser la résolution la plus récente');
 const requetesAvantDisponibilite=catalogueRequests.length;
 await js("window.__mainGridMutations=0;window.__mainGridObserver=new MutationObserver(rs=>window.__mainGridMutations+=rs.filter(r=>r.type==='childList').length);window.__mainGridObserver.observe($('grille-tous'),{childList:true,subtree:true});for(const v of ['disponibles','absents','tout','absents','disponibles','tout','disponibles','absents','tout'])document.querySelector('#cat-dispo [data-v='+v+']').click()");await sleep(100);
 assert.equal(await js('window.__mainGridMutations'),0,'Les filtres de disponibilité ne reconstruisent jamais la grille');
 assert.equal(catalogueRequests.length,requetesAvantDisponibilite,'Disponibilité filtre les cartes sans rappeler le catalogue');
 assert.ok(await js("[...$('grille-tous').children].some(c=>!c.classList.contains('filter-out'))"),'Disponibles conserve les résultats disponibles et tous les cartes visibles pour Tous');await js('window.__mainGridObserver.disconnect()');
 const parcoursAvantCriteres=catalogueRequests.filter(x=>x.startsWith('/catalogue/parcourir?')).length;
 await js("document.querySelector('#explore [data-t=cat-select][data-k=genre]').click();document.querySelector('#m-extra [data-t=cat-pf-set][data-k=genre][data-v=\"28\"]').click()");await sleep(260);
 assert.equal(await js("CAT.pf.genre"),'28','Filtre Genre principal');
 await js("document.querySelector('#explore [data-t=cat-select][data-k=annee]').click()");await sleep(100);
 const etatModaleAnnee=await js("JSON.stringify({vue:VUE,mode:!!$('cat-year-mode'),titre:$('m-titre')?.textContent,extra:$('m-extra')?.textContent,modalExiste:!!$('modal'),ouverte:$('modal')?!$('modal').classList.contains('hidden'):false,cible:CAT.anneeCible})").then(JSON.parse);
 assert.equal(etatModaleAnnee.mode,true,'Le clic Année ouvre son éditeur : '+JSON.stringify(etatModaleAnnee));
 await js("$('cat-year-mode').value='exact';$('cat-year-mode').dispatchEvent(new Event('change',{bubbles:true}));$('cat-year-value').value='2024';$('cat-year-value').dispatchEvent(new Event('change',{bubbles:true}))");
 assert.equal(await js("$('cat-year-value').value"),'2024','Année sélectionnée');
 await js("document.querySelector('[data-t=cat-year-apply]').click()");await sleep(100);
 assert.equal(await js("CAT.pf.annee"),'exact:2024','Filtre Année principal');
 await js("document.querySelector('#explore [data-t=cat-select][data-k=annee]').click()");await sleep(100);
 await js("$('cat-year-mode').value='between';$('cat-year-mode').dispatchEvent(new Event('change',{bubbles:true}));$('cat-year-start').value='2015';$('cat-year-end').value='2022'");
 assert.equal(await js("!$('cat-year-range').hidden"),true,'Le mode plage affiche ses deux années');
 for(const w of [320,360,390]){await send('Emulation.setDeviceMetricsOverride',{width:w,height:844,deviceScaleFactor:2,mobile:true});assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true,'Sélecteur de plage sans débordement à '+w+' px')}
 await js("document.querySelector('[data-t=cat-year-apply]').click()");await sleep(100);assert.equal(await js("CAT.pf.annee"),'between:2015:2022','Plage d’années appliquée au catalogue');await send('Emulation.setDeviceMetricsOverride',{width:1280,height:850,deviceScaleFactor:1,mobile:false});
 await js("document.querySelector('#explore [data-t=cat-select][data-k=tri]').click();document.querySelector('#m-extra [data-t=cat-pf-set][data-k=tri][data-v=note]').click()");await sleep(100);
 assert.equal(await js("CAT.pf.tri"),'note','Filtre Tri principal');
 assert.ok(catalogueRequests.filter(x=>x.startsWith('/catalogue/parcourir?')).length-parcoursAvantCriteres<=4,'Genre, années et tri produisent une requête par choix');
 const parcoursAvantTypes=catalogueRequests.filter(x=>x.startsWith('/catalogue/parcourir?')).length;
 await js("document.querySelector('#fchips [data-t=filtre][data-v=tv]').click();document.querySelector('#fchips [data-t=filtre][data-v=movie]').click()");await sleep(180);
 assert.equal(await js("CAT.filtre"),'movie','Changements rapides Films/Séries');
 assert.ok(catalogueRequests.filter(x=>x.startsWith('/catalogue/parcourir?')).length-parcoursAvantTypes<=2,'Les changements rapides de type ne laissent pas de rafale');
 await js("CAT.pf={genre:'',annee:'',tri:'popularite'};document.querySelector('#fchips [data-t=filtre][data-v=tv]').click()");await sleep(180);
 await js("for(const v of ['disponibles','absents','tout','absents','disponibles','tout'])document.querySelector('#cat-dispo [data-v='+v+']').click()");
 assert.equal(await js("CAT.filtre==='tv'&&document.querySelectorAll('#grille-tous .cat-card').length>0"),true,'Séries : filtres de disponibilité restent interactifs');
 await js("window.scrollTo(0,180);CAT.pf.genre='';CAT.pf.annee='';CAT.pf.tri='popularite';await majCatalogueConserverScroll(180)");await sleep(100);
 await js("const c=document.querySelector('#grille-tous .cat-card');c?.click()");await sleep(100);assert.equal(await js('FICHE_OUVERTE'),true,'Une affiche du Catalogue principal ouvre une fiche');await js('retourNav()');await sleep(250);assert.equal(await js("VUE"),'catalogue','Retour à la grille principale');
 assert.ok(catalogueRequests.length-nbRequetesCatalogueAvant<60,'Pas de boucle de requêtes catalogue');
 // Pages « Voir tout » : Chromium réel, filtres natifs, DOM stable, badges distincts et réseau mesuré.
 await js("CAT.tout=null;const b=document.createElement('button');Object.assign(b.dataset,{t:'cat-all',kind:'tendances',title:'Tendances',type:'all',context:'Films et séries'});document.body.appendChild(b);b.click();b.remove()");
 await sleep(250);
 const layoutDesktop=await js("JSON.stringify({title:$('cat-tout').querySelector('h1')?.textContent,filterHeight:$('cat-tout-filters').getBoundingClientRect().height,display:getComputedStyle($('cat-tout-filters')).display,items:$('cat-tout-grid').children.length,root:$('cat-tout-filters').className})").then(JSON.parse);
 assert.equal(layoutDesktop.title,'Tendances');assert.equal(layoutDesktop.display,'grid');assert.ok(layoutDesktop.filterHeight>100,'Les filtres secondaires sont visibles, sans panneau replié : '+JSON.stringify(layoutDesktop));assert.equal(layoutDesktop.items,4);
 await sleep(80);
 const badgeState=await js("JSON.stringify([...$('cat-tout-grid').children].map(c=>({id:c.dataset.id,status:c.querySelector('.mk')?.getAttribute('aria-label'),quality:[...c.querySelectorAll('.qual-i span')].map(x=>x.textContent),seen:c.querySelector('.vu-i')?.getAttribute('aria-label'),bookmark:c.querySelector('.vu-i')?.classList.contains('non-vu'),eye:!!c.querySelector('.vu-i svg path')})))").then(JSON.parse);
 assert.equal(badgeState.find(x=>x.id==='101').status,undefined);assert.deepEqual(badgeState.find(x=>x.id==='101').quality,['HD et 4K']);assert.equal(badgeState.find(x=>x.id==='101').seen,undefined,'Un contenu vu ne reçoit pas d’indicateur');assert.equal(badgeState.find(x=>x.id==='102').seen,'Non vu');assert.equal(badgeState.find(x=>x.id==='102').bookmark,true);assert.equal(badgeState.some(x=>x.eye),false);
 const networkAvantFiltres=catalogueRequests.length;
 await js("window.__kcFilterMutationCount=0;window.__kcFilterObserver=new MutationObserver(x=>window.__kcFilterMutationCount+=x.length);window.__kcFilterObserver.observe($('cat-tout-grid'),{childList:true})");
 await js("document.querySelector('#cat-tout-filters [data-k=type][data-v=tv]').click()");await sleep(80);
 assert.equal(await js("CAT.tout.filtres.type"),'tv');assert.equal(await js("[...$('cat-tout-grid').children].filter(c=>getComputedStyle(c).display!=='none').length"),2,'Films/Séries filtre les cartes sans recharger la liste');
 const changementNative=await js("(()=>{const s=$('cat-tout-filters').querySelector('select[data-k=genre]'),avant=CAT.tout.filtres.genre;s.click();return JSON.stringify({avant,apres:CAT.tout.filtres.genre,selection:s.value})})()").then(JSON.parse);
 assert.equal(changementNative.avant,'');assert.equal(changementNative.apres,'','Un clic pour ouvrir le select ne change pas la valeur');
 assert.equal(await js("!!$('cat-tout-filters').querySelector('[data-k=dispo]')"),false,'Pages secondaires sans Tous/Disponibles/Absents');
 await js("(()=>{const s=$('cat-tout-filters').querySelector('select[data-k=genre]');s.value='18';s.dispatchEvent(new Event('change',{bubbles:true}))})()");
 await js("$('cat-tout-filters').querySelector('[data-t=cat-all-year]').click()");await sleep(80);
 await js("$('cat-year-mode').value='exact';$('cat-year-mode').dispatchEvent(new Event('change',{bubbles:true}));$('cat-year-value').value='2025';$('cat-year-value').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[data-t=cat-year-apply]').click()");
 const filtresActifs=await js("JSON.stringify({f:CAT.tout.filtres,visible:[...$('cat-tout-grid').children].filter(c=>getComputedStyle(c).display!=='none').map(c=>c.dataset.id),hauteur:$('cat-tout-filters').getBoundingClientRect().height})").then(JSON.parse);
 assert.equal(filtresActifs.f.genre,'18');assert.equal(filtresActifs.f.annee,'exact:2025');assert.deepEqual(filtresActifs.visible,['201']);assert.ok(filtresActifs.hauteur>100);
 assert.equal(await js('window.__kcFilterMutationCount'),0,'Les filtres ne reconstruisent pas la grille ni ses cartes');await js('window.__kcFilterObserver.disconnect()');
 await js("(()=>{const t=$('cat-tout-filters').querySelector('select[data-k=tri]');t.value='note';t.dispatchEvent(new Event('change',{bubbles:true}))})()");assert.equal(await js('CAT.tout.filtres.tri'),'note','Tri des pages secondaires');
 assert.ok(catalogueRequests.length-networkAvantFiltres<=1,'Les filtres locaux ne déclenchent pas de rafale de requêtes');
 await js("window.__kcGridMutationCount=0;window.__kcGridObserver=new MutationObserver(x=>window.__kcGridMutationCount+=x.length);window.__kcGridObserver.observe($('cat-tout-grid'),{childList:true});appliquerEtats([...$('cat-tout-grid').children]);appliquerVus([...$('cat-tout-grid').children])");
 await js("appliquerEtats([...$('cat-tout-grid').children]);appliquerVus([...$('cat-tout-grid').children])");await sleep(30);
 assert.equal(await js('window.__kcGridMutationCount'),0,'Une nouvelle lecture identique des statuts ne reconstruit pas les cartes');
 await js("window.scrollTo(0,180);const c=$('cat-tout-grid').querySelector('[data-id=\"201\"]');c.click();");await sleep(100);
 assert.equal(await js('FICHE_OUVERTE'),true,'Une affiche de la catégorie ouvre sa fiche');
 await js('await retourFicheTest()');await sleep(250);
 assert.equal(await js('CAT.tout.filtres.genre'),'18','Le retour de fiche conserve le contexte filtré');
 await js('window.scrollTo(0,0)');await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 writeFileSync('/private/tmp/kc_catalogue_secondary_desktop.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 const mobileCatalogue=await js("JSON.stringify({overflow:document.documentElement.scrollWidth>innerWidth,filters:$('cat-tout-filters').getBoundingClientRect().height,selects:[...$('cat-tout-filters').querySelectorAll('select')].every(x=>x.getBoundingClientRect().width>0)})").then(JSON.parse);
 assert.equal(mobileCatalogue.overflow,false,'Pas de débordement mobile du Catalogue');assert.ok(mobileCatalogue.filters>100);assert.equal(mobileCatalogue.selects,true,JSON.stringify(mobileCatalogue));
 for(const width of [320,360,375,390]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:3,mobile:true});const row=await js("JSON.stringify({overflow:document.documentElement.scrollWidth>innerWidth,ys:[...$('cat-tout-filters').querySelectorAll('.cat-select-row>*')].map(x=>Math.round(x.getBoundingClientRect().top)),widths:[...$('cat-tout-filters').querySelectorAll('.cat-select-row>*')].map(x=>x.getBoundingClientRect().width)})").then(JSON.parse);assert.equal(row.overflow,false,'Aucun overflow filtre secondaire à '+width);if(width>=360)assert.equal(new Set(row.ys).size,1,'Genre, Année et Tri sur une ligne à '+width);else assert.ok(new Set(row.ys).size<=2,'Adaptation compacte à '+width);assert.ok(row.widths.every(x=>x>0),'Les trois contrôles sont visibles à '+width)}
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:3,mobile:true});
 writeFileSync('/private/tmp/kc_catalogue_secondary_mobile.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 for(const kind of ['ajoutes','demandes']){
  await js("CAT.tout=null;const b=document.createElement('button');Object.assign(b.dataset,{t:'cat-all',kind:'"+kind+"',title:'"+(kind==='ajoutes'?'Ajoutés récemment':'Demandes récentes')+"',context:'Test'});document.body.appendChild(b);b.click();b.remove()");await sleep(100);
  const pageFiltres=await js("JSON.stringify({title:$('cat-tout').querySelector('h1')?.textContent,height:$('cat-tout-filters').getBoundingClientRect().height,dispo:!!$('cat-tout-filters').querySelector('[data-k=dispo]'),genre:!!$('cat-tout-filters').querySelector('select[data-k=genre]')})").then(JSON.parse);
  assert.ok(pageFiltres.height>50,kind+' : filtres visibles');assert.equal(pageFiltres.dispo,false,kind+' : pas de disponibilité absurde');assert.equal(pageFiltres.genre,true);
 }
 await js("CAT.tout=null;CAT.filtre='tout';CAT.dispoMode='disponibles';CAT.init=true;renderCatalogue();await majCatalogue(true)");await sleep(180);
 assert.equal(await js("$('slot0')?.classList.contains('hidden')||$('slot1')?.classList.contains('hidden')"),false,'Découvrir conserve Ajoutés récemment et Demandes récentes avec un filtre principal mémorisé');
 await js("renderStatus({observe_a:200,en_cours:false,lecture:{actif:false},seance:{nature:'vide'},detail:{}});renderStatus({observe_a:100,en_cours:true});");
 assert.equal(await js('ST.observe_a'),200);
 // Fiche série : statuts épisode indépendants, toggle vu unique et actions de demande ciblées.
 await js("await ouvrirFiche('tv',42)");await sleep(250);
 assert.equal(await js("document.querySelectorAll('.fi-ep-request').length"),2,'Paolo expose uniquement les qualités réellement manquantes');
 assert.equal(await js("document.querySelector('.fi-ep-states')?.textContent.includes('HD + 4K')"),true,'Épisode HD + 4K fusionné');
 assert.equal(await js("document.querySelectorAll('.fi-ep-watched').length"),0,'Aucun libellé Vu redondant');
 assert.equal(await js("!!document.querySelector('.fi-ep-mark.on')"),true,'Le check rond est l’unique indicateur Vu');
 assert.match(await js("getComputedStyle(document.querySelector('.fi-ep-mark.on')).color"),/52, 195, 143/,'Le visionnage utilise le check vert discret');
 assert.equal(await js("!!document.querySelector('.fi-season-check')"),false,'La saison partiellement vue n’est pas marquée comme entièrement vue');
 assert.equal(await js("!!document.querySelector('[data-t=fi-season-request]')"),true,'Demande de saison visible pour les épisodes manquants');
 for(const width of [1280,390,360,320]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:width<500?3:1,mobile:width<500});const row=await js("JSON.stringify({overflow:document.documentElement.scrollWidth>innerWidth,season:document.querySelector('.fi-seasons')?.getBoundingClientRect().height,request:document.querySelector('.fi-season-request')?.getBoundingClientRect().width,episodes:document.querySelectorAll('.fi-episode').length})").then(JSON.parse);assert.equal(row.overflow,false,'Fiche série sans débordement à '+width+'px');assert.equal(row.episodes,4);assert.ok(row.season>0);assert.ok(row.request>0,'Action de saison accessible à '+width+'px')}
 await js("document.querySelector('.fi-episode [data-t=fi-ep-vu][data-e=\"2\"]').click()");await sleep(120);
 assert.equal(await js("document.querySelector('.fi-episode [data-t=fi-ep-vu][data-e=\"2\"]').getAttribute('aria-pressed')"),'true','Le toggle conserve sa capacité à marquer Vu');
 await js("document.querySelector('.fi-episode [data-t=fi-ep-vu][data-e=\"2\"]').click()");await sleep(100);
 assert.equal(await js("document.querySelector('.fi-episode [data-t=fi-ep-vu][data-e=\"2\"]').getAttribute('aria-pressed')"),'false','Le toggle conserve sa capacité à marquer Non vu');
 await js("document.querySelector('[data-t=fi-ep-request][data-e=\"2\"]').click()");await sleep(30);
 assert.match(await js("$('m-texte').textContent"),/l’épisode 2 de « Paolo » en 4K/,'La modalité précise l’épisode et uniquement la qualité manquante');
 await js("$('m-oui').click()");await sleep(80);
 assert.deepEqual(demandesEpisodes[0],{type:'tv',id:42,tvdb:84,saison:1,episode:2,qualites:['uhd']},'La demande d’épisode garde la qualité UHD ciblée');
 await js("document.querySelector('[data-t=fi-season-request]').click()");await sleep(30);
 assert.match(await js("$('m-texte').textContent"),/la saison 1 de « Paolo » en HD et 4K/);
 await js("$('m-oui').click()");await sleep(80);
 assert.deepEqual(demandesEpisodes[1],{type:'tv',id:42,tvdb:84,saison:1,qualites:['hd','uhd']},'La demande saisonnière transmet les deux qualités manquantes en un seul appel');
 const traktUi=await js("(()=>{const old=TRK;TRK={configuree:false,connectee:false,client_id:'',profil:null,code:null};const initial=traktHTML();TRK={configuree:true,connectee:false,client_id:'client-public',profil:null,code:'ABCD1234',activationUrl:'https://trakt.tv/activate'};const off=traktHTML();TRK={configuree:true,connectee:true,client_id:'',profil:{username:'cine-user',name:'Ciné User',avatar:'https://images.trakt.tv/avatar.jpg'}};const on=traktHTML();TRK=old;return JSON.stringify({initial,off,on})})()").then(JSON.parse);
 assert.match(traktUi.initial,/Non connecté/);assert.match(traktUi.initial,/id=\"trk-secret\" type=\"password\"/);assert.match(traktUi.initial,/Enregistrer et connecter/);
 assert.match(traktUi.off,/Non connecté/);assert.match(traktUi.off,/id=\"trk-id\"/);assert.match(traktUi.off,/id=\"trk-secret\" type=\"password\"/);assert.match(traktUi.off,/data-t=\"trk-setup\"/);assert.match(traktUi.off,/Connecter Trakt/);assert.match(traktUi.off,/trakt\.tv\/activate/);assert.equal((traktUi.off.match(/data-t=\"trk-setup\"/g)||[]).length,1,'Une seule action de connexion');
 assert.match(traktUi.on,/cine-user/);assert.match(traktUi.on,/Ciné User/);assert.match(traktUi.on,/images\.trakt\.tv\/avatar\.jpg/);assert.match(traktUi.on,/Connecté/);assert.match(traktUi.on,/data-t=\"trk-off\"/);assert.doesNotMatch(traktUi.on,/trk-id|trk-secret|Client Secret|Client ID/,'Les champs et secrets disparaissent après connexion');
 for(const width of [390,1280]){await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:width<500?3:1,mobile:width<500});const traktLayout=await js("(()=>{const ancien=TRK;TRK={configuree:true,connectee:true,client_id:'',profil:{username:'utilisateur-long-pour-tester-le-responsive',name:'Nom de compte assez long pour tester',avatar:''}};const h=document.createElement('div');h.style.cssText='width:100%;max-width:600px';h.innerHTML=traktHTML();document.body.appendChild(h);const p=h.querySelector('.trakt-profile'),a=h.querySelector('.trakt-profile-actions'),c=h.querySelector('.trakt-profile-copy');const r={width:p.getBoundingClientRect().width,scroll:p.scrollWidth,actions:a.getBoundingClientRect().width,copy:c.getBoundingClientRect().width,button:a.querySelector('button')?.getBoundingClientRect().width};h.remove();TRK=ancien;return JSON.stringify(r)})()").then(JSON.parse);assert.ok(traktLayout.width>0&&traktLayout.scroll<=traktLayout.width,'Carte Trakt sans débordement à '+width+'px');assert.ok(traktLayout.actions>0&&traktLayout.copy>0&&traktLayout.button>0,'Profil et action Déconnecter visibles à '+width+'px: '+JSON.stringify(traktLayout))}
 await js("(async()=>{const ancien=window.fetch;window.fetch=async()=>({ok:true,status:200,json:async()=>({ok:true,configuree:true,connectee:true,client_id:null,profil:{username:'persisted-user',name:'Persisted User',avatar:''}})});await majTrakt();window.fetch=ancien;clearInterval(TRK.profileTim);TRK.profileTim=null})()");
 const majTraktState=await js("JSON.stringify({configuree:TRK.configuree,connectee:TRK.connectee,profil:TRK.profil})").then(JSON.parse);
 assert.deepEqual(majTraktState,{configuree:true,connectee:true,profil:{username:'persisted-user',name:'Persisted User',avatar:''}},'Réglages reprend l’état et le profil persistés sans rechargement');
 const decompte=await js("JSON.stringify(await (async()=>{const t=$('toast'),textes=[];const o=new MutationObserver(()=>textes.push(t.textContent.replace(' ×','')));o.observe(t,{childList:true,characterData:true,subtree:true});const debut=performance.now();await decompteSeance();o.disconnect();return {textes,duree:performance.now()-debut,visible:t.classList.contains('show')}})())").then(JSON.parse);
 assert.deepEqual(decompte.textes,['La séance démarre dans 3…','La séance démarre dans 2…','La séance démarre dans 1…','La séance démarre…']);assert.ok(decompte.duree>=2900&&decompte.duree<4000,'Le décompte reste proche de sa durée de trois secondes : '+decompte.duree+' ms');assert.equal(decompte.visible,true,'Le toast est maintenu visible jusqu’à la fin du décompte');
 await js("decompteSeance();window.toastPointer=getComputedStyle($('toast')).pointerEvents;const b=document.querySelector('[data-t=seance-cancel-launch]');window.cancelButtonReady=typeof b.onclick==='function';setTimeout(()=>b.onclick(new MouseEvent('click',{bubbles:true})),80)");await sleep(350);
 assert.equal(await js('window.toastPointer'),'auto','Le toast de décompte reste tactilement interactif');
 assert.equal(await js('window.cancelButtonReady'),true,'La croix est liée à la fonction d’annulation');
 assert.equal(cancelRequests,1,'La croix envoie une seule vraie demande d’annulation au serveur');
 assert.equal(await js("$('toast').textContent"),'Lancement annulé','L’annulation stoppe le décompte et confirme la réponse du serveur');
 await sleep(1050);assert.equal(await js("$('toast').textContent"),'Lancement annulé','Aucun lancement différé après annulation');
 }
 if(!process.env.KAMCINE_275_ONLY){
 // History navigation uses the same browser and media fixtures as the existing journeys.
 for(const width of [320,390,1280]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:850,deviceScaleFactor:1,mobile:width<700});
  for(const [view,path] of Object.entries({seance:'/home',catalogue:'/catalogue',telechargements:'/downloads',appareils:'/devices',profil:'/profile',reglages:'/settings'})){
   await js(`await onglet('${view}')`);assert.equal(await js('location.pathname'),path);
  }
  await js("TMDBOK=true;CAT.q='';CAT.filtre='tout';CAT.dispoMode='tout';CAT.vuMode='tous';CAT.init=false;CAT.tout=null;onglet('catalogue');renderCatalogue();await majCatalogue()");await sleep(150);await js('window.scrollTo(0,400)');await sleep(150);
  const position=await js('scrollY');assert.ok(position>50,'Catalogue has a scrollable position at '+width);
  for(const type of ['movie','tv']){
   await js(`await ouvrirFiche('${type}',${type==='movie'?1:34},${type==='movie'?'null,null':'1,2'})`);await sleep(100);
   assert.equal(await js('location.pathname'),type==='movie'?'/films/1':'/series/34');
   await js("$('fiche').scrollTop=180");await sleep(100);const detailPosition=await js("$('fiche').scrollTop");await js('history.back()');await sleep(250);
   assert.equal(await js('location.pathname'),'/catalogue');assert.equal(await js('FICHE_OUVERTE'),false);
   assert.ok(Math.abs(await js('scrollY')-position)<3,'Detail back restores catalogue at '+width);
   await js('history.forward()');await sleep(250);
   assert.equal(await js('FICHE_OUVERTE'),true);assert.equal(await js('FI.type'),type);assert.ok(Math.abs(await js("$('fiche').scrollTop")-detailPosition)<3,'Forward restores detail scroll');
   await js('await retourFicheTest()');await sleep(250);
  }
  await js("await ouvrirFiche('movie',1);$('fiche').scrollTop=120;saveNavigation();ouvrirPersonne(42)");await sleep(250);
  assert.equal(await js('location.pathname'),'/people/42');
  await js("await ouvrirFiche('movie',2);retourNav()");await sleep(250);
  assert.equal(await js('location.pathname'),'/people/42');
  await js('await retourFicheTest()');await sleep(250);assert.equal(await js('location.pathname'),'/films/1');
  await js('await retourFicheTest()');await sleep(250);assert.ok(Math.abs(await js('scrollY')-position)<3);
  await js("await ouvrirFiche('movie',1);onglet('telechargements')");await sleep(250);
  assert.equal(await js('location.pathname'),'/downloads');assert.equal(await js('FICHE_OUVERTE'),false);
  await js('history.back()');await sleep(250);assert.equal(await js('location.pathname'),'/films/1');assert.equal(await js('FICHE_OUVERTE'),true);
  writeFileSync('/private/tmp/kc_navigation_'+width+'.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 }
 for(const path of ['/settings','/catalogue','/profile','/downloads','/devices','/home','/films/1','/series/34?season=1&episode=2','/people/42']){
  await send('Page.navigate',{url:'http://127.0.0.1:8897'+path});await sleep(1200);
  assert.equal(await js('location.pathname+location.search'),path,'Direct URL '+path);
  assert.equal(await js('NAV.ready&&!NAV.restoring'),true);
  await send('Page.reload');await sleep(1200);
  assert.equal(await js('location.pathname+location.search'),path,'Reload '+path);
  if(path.startsWith('/series/'))assert.equal(await js('FI.qe'),2);
 }
 await send('Page.navigate',{url:'http://127.0.0.1:8897/?kc_push='+encodeURIComponent(JSON.stringify({id:'1',cible:{page:'telechargements'}}))});await sleep(1600);
 assert.equal(await js('location.pathname'),'/downloads','Notification destination survives route initialization');
 console.log('Navigation : six écrans, film, série, personne, précédent et suivant, retours imbriqués, scroll à 320/390/1280 px, liens directs et rechargements validés.');
 }
 const freshTarget=await send('Target.createTarget',{url:'about:blank'});
 await send('Target.closeTarget',{targetId});
 ({sessionId:session}=await send('Target.attachToTarget',{targetId:freshTarget.targetId,flatten:true},null));
 await send('Runtime.enable');await send('Page.enable');await send('Log.enable');
 navigationFixture=true;
 for(let i=0;i<40;i++)catalogueItems.push({id:600+i,type:'movie',titre:'Film test '+i,annee:'2024',note:6+i/100,genres:[28],affiche:null});
 const waitFor=async expr=>{for(let i=0;i<120;i++){try{if(await js(expr))return}catch(e){}await sleep(50)}throw new Error('Délai : '+expr)};
 const navigate=async path=>{await send('Page.navigate',{url:'http://127.0.0.1:8897'+path});await sleep(350);await waitFor('NAV.ready&&!NAV.restoring')};
 const click=async selector=>{await js(`document.querySelector(${JSON.stringify(selector)}).click()`);await sleep(80)};
 const traverse=async direction=>{await js(`await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Historique sans popstate')),6000);addEventListener('popstate',()=>{clearTimeout(timer);resolve()},{once:true});history.${direction}()})`);await waitFor('!NAV.restoring')};
 const back=()=>traverse('back');
 const forward=()=>traverse('forward');
 for(const width of [320,390,1440]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<700});
  await navigate('/catalogue');
  await click('[data-t="filtre"][data-v="movie"]');await waitFor("$('grille-tous')?.children.length>=8&&!CAT.charge");
  await js("ouvrirSelectCatalogue('genre','movie')");await click('[data-t="cat-pf-set"][data-v="28"]');await waitFor('CAT.pf.genre==="28"&&!CAT.charge');
  await js("ouvrirSelectCatalogue('tri','movie')");await click('[data-t="cat-pf-set"][data-v="note"]');await waitFor('CAT.pf.tri==="note"&&!CAT.charge');
  const beforeYear=await js('location.pathname+location.search');
  await js("ouvrirSelectCatalogue('annee','movie');$('cat-year-mode').value='exact';$('cat-year-value').value='2024'");
  await click('[data-t="cat-year-apply"]');await waitFor('CAT.pf.annee==="exact:2024"&&!CAT.charge');
  const filteredURL=await js('location.pathname+location.search');
  assert.match(filteredURL,/type=movie/);assert.match(filteredURL,/genre=28/);assert.match(filteredURL,/sort=note/);assert.match(filteredURL,/year=exact%3A2024/);
  await js("await chargerPlus('movie',CAT.tok)");await sleep(150);await js('window.scrollTo(0,440)');await sleep(250);
  const snapshot=await js("JSON.stringify({filters:CAT.pf,ids:[...document.querySelectorAll('#grille-tous .cat-card')].map(c=>c.dataset.cle),scroll:scrollY})").then(JSON.parse);
  assert.ok(snapshot.scroll>100,'Catalogue actually scrolled at '+width);
  await click('#grille-tous .cat-card');await waitFor('FICHE_OUVERTE&&FI.pret');
  await back();assert.equal(await js('location.pathname+location.search'),filteredURL);
  assert.deepEqual(await js('CAT.pf'),snapshot.filters);
  assert.deepEqual(await js("[...document.querySelectorAll('#grille-tous .cat-card')].map(c=>c.dataset.cle)"),snapshot.ids);
  assert.ok(Math.abs(await js('scrollY')-snapshot.scroll)<3,'Exact catalogue scroll from detail '+width);
  await forward();assert.equal(await js('FICHE_OUVERTE'),true);await back();
  await back();assert.equal(await js('location.pathname+location.search'),beforeYear);assert.equal(await js('CAT.pf.annee'),'');
  await forward();assert.equal(await js('location.pathname+location.search'),filteredURL);assert.ok(Math.abs(await js('scrollY')-snapshot.scroll)<3);
  await send('Page.reload');await sleep(350);await waitFor('NAV.ready&&!NAV.restoring');
  assert.deepEqual(await js('CAT.pf'),snapshot.filters);assert.equal(await js('location.pathname+location.search'),filteredURL);
  assert.ok(Math.abs(await js('scrollY')-snapshot.scroll)<3,'Reload keeps pagination and scroll '+width);
  const sequence=await js('NAV.sequence');
  await js("$('cat-q').value='fi';$('cat-q').dispatchEvent(new Event('input',{bubbles:true}))");await sleep(100);
  await js("$('cat-q').value='film';$('cat-q').dispatchEvent(new Event('input',{bubbles:true}))");await sleep(550);
  assert.equal(await js('NAV.sequence'),sequence+1,'One history entry for a search, no entry per keystroke');
  assert.equal(await js("new URLSearchParams(location.search).get('q')"),'film');
  await js("$('cat-q').value='Film test';$('cat-q').dispatchEvent(new Event('input',{bubbles:true}))");await sleep(550);
  assert.equal(await js('NAV.sequence'),sequence+1,'Search refinement replaces the current search');
  await back();assert.equal(await js('location.pathname+location.search'),filteredURL);assert.equal(await js('CAT.q'),'');
  await forward();assert.equal(await js('CAT.q'),'Film test');assert.equal(await js("$('cat-q').value"),'Film test');
  const searchIDs=await js("[...document.querySelectorAll('#cat-search-grid .cat-card')].map(c=>c.dataset.cle)");
  await send('Page.reload');await sleep(350);await waitFor('NAV.ready&&!NAV.restoring');assert.equal(await js('CAT.q'),'Film test');
  assert.deepEqual(await js("[...document.querySelectorAll('#cat-search-grid .cat-card')].map(c=>c.dataset.cle)"),searchIDs,'Reload replays every search page without skipping one');
  await navigate('/catalogue?type=tv&view=genres&availability=disponibles&seen=vus');
  assert.equal(await js('CAT.filtre'),'tv');assert.equal(await js('CAT.vue.tv'),'genres');assert.equal(await js('CAT.dispoMode'),'disponibles');assert.equal(await js('CAT.vuMode'),'vus');
  await navigate('/catalogue?list=tendances&source=movie&type=movie&genre=28&sort=note');
  await waitFor("$('cat-tout-grid')?.children.length>0");
  assert.equal(await js('CAT.tout.kind'),'tendances');assert.equal(await js('CAT.tout.filtres.tri'),'note');assert.equal(await js("document.querySelector('#cat-tout-filters select[data-k=genre]').value"),'28','Restored genre visible in the selector');
  await click('#cat-tout-grid .cat-card');await waitFor('FICHE_OUVERTE&&FI.pret');await back();assert.equal(await js('CAT.tout.kind'),'tendances');assert.equal(await js('CAT.tout.filtres.genre'),'28');
  await send('Page.reload');await sleep(350);await waitFor('NAV.ready&&!NAV.restoring');assert.equal(await js('CAT.tout.kind'),'tendances');assert.equal(await js('CAT.tout.filtres.tri'),'note');
  assert.equal(await js("/scroll|page=|token|filtersOpen/.test(location.search)"),false,'Temporary state stays out of the URL');
 }
 await navigate('/catalogue?type=invalid&sort=unknown&year=between:1:9999&genre=nope&view=oops&availability=bad&seen=bad&page=99');
 assert.equal(await js('location.pathname+location.search'),'/catalogue','Invalid and unknown parameters normalize to defaults');
 await navigate('/catalogue?q='+encodeURIComponent('Été & retour <script>'));
 assert.equal(await js('CAT.q'),'Été & retour <script>');assert.equal(await js("$('cat-q').value"),'Été & retour <script>');
 assert.equal(await js("document.querySelectorAll('#catalogue-body script').length"),0);
 await navigate('/catalogue?list=tendances&source=movie&type=tv');
 assert.equal(await js('CAT.tout.type'),'movie');assert.equal(await js('CAT.tout.filtres.type'),'tv');
 await send('Page.reload');await sleep(350);await waitFor('NAV.ready&&!NAV.restoring');assert.equal(await js('CAT.tout.filtres.type'),'tv');
 await navigate('/catalogue?list=tendances&source=movie&type=all');assert.equal(await js('CAT.tout.filtres.type'),'tout');
 await navigate('/catalogue?list=favoris-films&sort=note');assert.equal(await js('CAT.tout.kind'),'profil-local');assert.equal(await js('CAT.tout.id'),'favoris-films');
 console.log('Catalogue : URLs utiles, historique des filtres, recherche groupée, retour exact avec ordre et scroll, pagination après reload, sous listes et paramètres invalides validés à 320/390/1440 px.');
 dlReponse={code:200,corps:{ok:true,configuree:true,active:true,en_cours:Array.from({length:4},(_,i)=>({hash:String(i+1).repeat(40),nom:'Film exemple avec un nom de fichier assez long '+i,etat:'téléchargement',progres:25+i*10,taille:8000000000,ajoute:1,media_type:'movie',media_id:600+i})),termines:[]}};
 for(const width of [320,360,390,768,1024,1440,1920]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<700});
  await navigate('/home');
  for(const view of ['seance','catalogue','appareils','reglages','profil','telechargements']){
   await js(`CAT.q='';CAT.tout=null;CAT.filtre='tout';CAT.pf={genre:'',annee:'',tri:'popularite'};CAT.dispoMode='tout';CAT.vuMode='tous';CAT.init=false;SET.cat=null;await onglet('${view}')`);await sleep(100);
   assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true,view+' horizontal overflow '+width);
   if(width>=900){
    assert.ok(await js("document.querySelector('.view.on').getBoundingClientRect().width")>800,'Desktop uses its width '+view);
    if(view==='reglages')assert.equal(await js("getComputedStyle(document.querySelector('.cat-list')).gridTemplateColumns.split(' ').length"),2);
    if(view==='telechargements')assert.equal(await js("getComputedStyle(document.querySelector('.dl-list')).gridTemplateColumns.split(' ').length"),2);
    if(view==='appareils')assert.equal(await js("getComputedStyle(document.querySelector('.device-sections')).gridTemplateColumns.split(' ').length"),2);
    if(view==='seance'){assert.equal(await js("getComputedStyle($('v-seance')).display"),'grid');assert.ok(await js("$('sc-vide').getBoundingClientRect().left<$('home-ajoutes').getBoundingClientRect().left"),'Main Home card starts in the first column');}
   }else if(view==='seance')assert.equal(await js("getComputedStyle($('v-seance')).display"),'flex');
   if([390,1440,1920].includes(width))writeFileSync('/private/tmp/kc275_'+view+'_'+width+'.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  await js("onglet('reglages');SET.cat='programmation';renderSettings()");
  assert.ok((await js("$('reglages-body').textContent")).includes('Programmation'));
  if(width>=900)assert.ok(await js("$('reglages-body').getBoundingClientRect().width")<=840);
  await js("ouvrirModal({titre:'Une action à confirmer',texte:'Une explication qui reste lisible sur mobile et desktop.',delai:0,oui:'Confirmer',onOui:()=>{}})");await sleep(200);
  assert.equal(await js("document.querySelector('#modal .sheet').scrollWidth<=document.querySelector('#modal .sheet').clientWidth"),true);
  const modal=await js("(()=>{const r=document.querySelector('#modal .sheet').getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,height:r.height}})()");
  assert.ok(modal.left>=0&&modal.right<=width&&modal.height<1000);if(width>=900)assert.equal(modal.width,560);
  await js("$('m-non').click()");await sleep(250);
  for(const type of ['movie','tv']){
   await js(`await ouvrirFiche('${type}',${type==='movie'?1:42});FI.meta.synopsis='Un récit assez long pour vérifier la largeur de lecture. '.repeat(18);FI.infos=[{k:'Production',v:'Studio de cinéma'},{k:'Pays',v:'France'}];renderFiche()`);await sleep(150);
   assert.equal(await js("$('fiche').scrollWidth<=$('fiche').clientWidth"),true,type+' detail overflow '+width);
   assert.ok(await js("document.querySelector('.pg-in').getBoundingClientRect().right<=innerWidth"));
   assert.ok(await js("document.querySelector('.pg-bar-in').getBoundingClientRect().width")<=760);
   if(width>=900){assert.ok(await js("document.querySelector('.pg-in').getBoundingClientRect().width")>900);if(type==='tv')assert.equal(await js("getComputedStyle(document.querySelector('.fi-episodes')).gridTemplateColumns.split(' ').length"),2);}
   if([390,1440].includes(width))writeFileSync('/private/tmp/kc275_'+type+'_'+width+'.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
   await js('onglet("seance")');
  }
 }
 console.log('Responsive : six écrans, catégories de Réglages, modales et fiches film/série contrôlés à 320/360/390/768/1024/1440/1920 px. Captures mobile et desktop produites.');
 assert.equal(errors.length,0,JSON.stringify(errors));
 if(!process.env.KAMCINE_NAV_ONLY&&!process.env.KAMCINE_275_ONLY)console.log('Séance : confirmation, pause, entracte, reprise, mémoire, pilotage, navigation, fichiers, notifications, actualisation et cinq largeurs validés.');
}finally{clearTimeout(timer);chrome.kill();server.closeAllConnections?.();server.close()}
// Toutes les connexions du serveur simulé sont fermées : le processus se termine au lieu d'attendre une connexion encore ouverte.
process.exit(0);
