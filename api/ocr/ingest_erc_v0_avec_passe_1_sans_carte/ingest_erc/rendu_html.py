"""Écran de validation BE (maquette v0) généré depuis la sortie de la passe 1."""
from __future__ import annotations

import json
import sys
from pathlib import Path

GABARIT = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Validation du référentiel — __TITRE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--papier:#F3F4F0;--carte:#FFFFFF;--encre:#1B1F1C;--gris:#5E665F;--trait:#D9DDD5;
--pin:#1F4D36;--pin-l:#E3EEE7;--ajonc:#B98300;--ajonc-l:#FBF0D4;--lande:#A8402E;--lande-l:#F8E4DF;--bruyere:#6D4E73}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--papier:#121614;--carte:#1A201D;--encre:#E6EAE5;--gris:#9AA39C;--trait:#2E3631;
--pin:#7CC49B;--pin-l:#1D3328;--ajonc:#E8B74A;--ajonc-l:#3A3017;--lande:#E88A77;--lande-l:#3D2320;--bruyere:#C3A3C9}}
:root[data-theme="dark"]{--papier:#121614;--carte:#1A201D;--encre:#E6EAE5;--gris:#9AA39C;--trait:#2E3631;
--pin:#7CC49B;--pin-l:#1D3328;--ajonc:#E8B74A;--ajonc-l:#3A3017;--lande:#E88A77;--lande-l:#3D2320;--bruyere:#C3A3C9}
*{box-sizing:border-box}body{margin:0;background:var(--papier);color:var(--encre);font:15px/1.55 "Public Sans",system-ui,sans-serif}
header{padding:28px 32px 20px;border-bottom:1px solid var(--trait)}
h1{font-size:26px;line-height:1.2;margin:0 0 6px;font-weight:700;letter-spacing:-.01em;max-width:60ch}
h2{font-size:18px;margin:0 0 12px;font-weight:600}
.sous{color:var(--gris);max-width:75ch;margin:0}
.bilan{display:flex;flex-wrap:wrap;gap:24px;margin-top:18px}
.bilan div{min-width:120px}.bilan b{display:block;font-size:22px;font-weight:600}
.bilan span{color:var(--gris);font-size:13px}
main{display:grid;grid-template-columns:minmax(320px,420px) 1fr;gap:28px;padding:24px 32px 60px;align-items:start}
@media (max-width:980px){main{grid-template-columns:1fr;padding:20px 16px}}
.file{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto;padding-right:4px}
@media (max-width:980px){.file{position:static;max-height:none}}
.avance{height:6px;background:var(--trait);border-radius:3px;margin:6px 0 16px;overflow:hidden}
.avance i{display:block;height:100%;background:var(--pin);width:0;transition:width .25s}
.q{background:var(--carte);border:1px solid var(--trait);border-left:4px solid var(--ajonc);padding:14px 14px 12px;margin-bottom:12px;border-radius:0 8px 8px 0}
.q.fait{border-left-color:var(--pin)}.q .portee{font-size:12px;color:var(--bruyere);font-weight:600}
.q p{margin:4px 0 10px;overflow-wrap:anywhere}.q label{display:flex;gap:8px;align-items:flex-start;padding:5px 6px;border-radius:6px;cursor:pointer}
.q label:hover{background:var(--papier)}.q input[type=text]{width:100%;padding:6px 8px;border:1px solid var(--trait);border-radius:6px;background:var(--papier);color:var(--encre);font:inherit}
.src{font-size:12px;color:var(--gris);margin-top:6px;overflow-wrap:anywhere}
section{background:var(--carte);border:1px solid var(--trait);border-radius:10px;padding:18px 18px 8px;margin-bottom:22px}
.defile{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th{text-align:left;font-weight:600;color:var(--gris);font-size:13px;padding:6px 10px;border-bottom:1px solid var(--trait);white-space:nowrap}
td{padding:0;border-bottom:1px solid var(--trait);vertical-align:top}
.c{padding:7px 10px;border-left:3px solid transparent;min-width:90px}
.c.haute{border-left-color:var(--pin)}.c.moyenne{border-left-color:var(--trait)}
.c.basse{border-left-color:var(--ajonc);background:var(--ajonc-l)}
.c .n{display:block;font-size:12px;color:var(--gris)}
.c[title]{cursor:help}
.code{font-weight:600;padding:7px 10px;white-space:nowrap}
.legende{display:flex;gap:16px;font-size:13px;color:var(--gris);margin:0 0 12px;flex-wrap:wrap}
.legende i{display:inline-block;width:10px;height:14px;vertical-align:-2px;margin-right:5px}
.ctl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:14px;padding-bottom:10px}
.pastille{font-size:12px;font-weight:600;padding:1px 8px;border-radius:10px;white-space:nowrap;align-self:start;margin-top:2px}
.ok{background:var(--pin-l);color:var(--pin)}.alerte{background:var(--lande-l);color:var(--lande)}.info{background:var(--papier);color:var(--gris)}
button{font:inherit;font-weight:600;background:var(--pin);color:var(--papier);border:0;border-radius:8px;padding:10px 16px;cursor:pointer}
button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,input:focus-visible{outline:2px solid var(--bruyere);outline-offset:2px}
pre{white-space:pre-wrap;font-size:12px;background:var(--papier);padding:12px;border-radius:8px;max-height:360px;overflow:auto}
details summary{cursor:pointer;font-weight:600;padding:4px 0}
</style></head><body>
<header>
<h1 id="titre"></h1>
<p class="sous">Nous avons lu les documents du dossier et pré-rempli le référentiel du projet. Répondez aux questions à gauche, vérifiez les cellules surlignées, puis validez : l'extraction détaillée (calendrier, budget) partira de ce référentiel.</p>
<div class="bilan" id="bilan"></div>
</header>
<main>
<aside class="file" aria-label="Questions">
<h2 id="qtitre">Questions</h2><div class="avance"><i id="barre"></i></div>
<div id="questions"></div>
<button id="valider" disabled>Valider le référentiel</button>
</aside>
<div>
<p class="legende"><span><i style="background:var(--pin)"></i>Confirmé par 2 sources</span><span><i style="background:var(--trait)"></i>Une source ou déduit</span><span><i style="background:var(--ajonc)"></i>À vérifier</span><span>Survolez une cellule pour voir ses sources.</span></p>
<section><h2>Projet</h2><div class="defile"><table id="tprojet"></table></div></section>
<section><h2>Unités de gestion</h2><div class="defile"><table id="tug"></table></div></section>
<section><h2>Actions</h2><div class="defile"><table id="tact"></table></div></section>
<section><h2>Contrôles croisés</h2><div id="ctl"></div></section>
<section><h2>Documents lus</h2><div id="docs"></div></section>
<section id="sortie" hidden><h2>Référentiel validé</h2><p class="sous">Ces valeurs repartent dans le pipeline comme une saisie manuelle (origine « user »).</p><pre id="json"></pre></section>
</div></main>
<script>
const D=__DONNEES__;
const R=D.referentiel, rep={};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=v=>v==null?"—":Array.isArray(v)?v.join(", "):typeof v==="object"?Object.values(v).join(" · "):v;
const srcTxt=c=>(c.sources||[]).map(s=>`${s.doc} ${s.loc}${s.extrait?" — "+s.extrait:""}`).join("\n");
const cell=(c,aff)=>`<div class="c ${c.confiance}" title="${esc(srcTxt(c))}">${esc(aff?aff(c.valeur):fmt(c.valeur))}${c.note?`<span class="n">${esc(c.note)}</span>`:""}</div>`;
const LIB={nom:"Nom",maitre_ouvrage:"Maître d'ouvrage",reference_decision:"Référence de la décision",date_decision:"Date de la décision",
type_procedure:"Procédure",commune:"Commune",annee_fin:"Fin des obligations",annee_etat_zero:"Année de l'état zéro",annee_N:"Année N",
duree_ans:"Durée (ans)",convention_montants:"Montants"};
document.getElementById("titre").textContent=R.projet.nom?R.projet.nom.valeur:"Nouveau projet";
const st=R.stats, conf=st.confiance;
document.getElementById("bilan").innerHTML=[
 [st.cellules,"cellules pré-remplies"],[conf.haute||0,"confirmées par 2 sources"],[conf.basse||0,"à vérifier"],
 [R.questions.length,"questions"],[st.geomce_pret_pct+" %","des champs GéoMCE socle renseignés"]]
 .map(([a,b])=>`<div><b>${a}</b><span>${b}</span></div>`).join("");
document.getElementById("tprojet").innerHTML="<tr><th>Champ</th><th>Valeur proposée</th></tr>"+
 Object.entries(R.projet).map(([k,c])=>`<tr><td class="code">${LIB[k]||k}</td><td>${cell(c)}</td></tr>`).join("");
const zone=v=>v?v.replace("::"," · filtre "):null;
document.getElementById("tug").innerHTML="<tr><th>UG</th><th>Libellé</th><th>Type ERC</th><th>Géométrie</th><th>Surface (ha)</th></tr>"+
 R.ugs.map(u=>`<tr><td class="code">${u.ug_code}</td><td>${cell(u.libelle)}</td><td>${cell(u.type_erc)}</td><td>${cell(u.zone_sig,zone)}</td><td>${cell(u.surface_ha)}</td></tr>`).join("");
document.getElementById("tact").innerHTML="<tr><th>Code</th><th>Intitulé</th><th>Nature</th><th>UG</th><th>Cible</th></tr>"+
 R.actions.map(a=>`<tr><td class="code">${a.code}</td><td>${cell(a.intitule)}</td><td>${cell(a.nature)}</td><td>${cell(a.ugs)}</td><td>${cell(a.cible)}</td></tr>`).join("");
const LS={ok:"Conforme",alerte:"À vérifier",info:"Info"};
document.getElementById("ctl").innerHTML='<div class="ctl">'+R.controles.map(c=>`<span class="pastille ${c.statut}">${LS[c.statut]||c.statut}</span><div><b>${esc(c.controle)}</b> — ${esc(c.detail)}</div>`).join("")+"</div>";
document.getElementById("docs").innerHTML='<div class="ctl">'+D.documents.map(d=>`<span class="pastille info">${esc(d.role)}</span><div><b>${esc(d.nom)}</b> — ${esc(d.indice)}${d.pages?` · ${d.pages} p. dont ${d.pages_scannees} scannées`:""}${(d.sous_documents||[]).map(a=>`<br>Annexe ${a.num} p.${a.page_debut}-${a.page_fin} : ${esc(a.titre)}${a.doublon_de?" — doublon ignoré":""}`).join("")}</div>`).join("")+
 Object.entries(D.selection_pdf).map(([n,s])=>`<span class="pastille info">sélection</span><div>${esc(n)} : ${s.retenues.length} sections envoyées (${s.car_retenus.toLocaleString("fr")} caractères sur ${s.car_total.toLocaleString("fr")}), familles de codes ${Object.keys(s.familles).join(", ")}</div>`).join("")+"</div>";
const zq=document.getElementById("questions");
zq.innerHTML=R.questions.map(q=>{
 const opts=[...(q.options||[])];
 const radios=opts.map((o,i)=>`<label><input type="radio" name="${q.id}" value="${esc(o)}" ${o===q.proposition?"data-prop":""}>${esc(o)}${o===q.proposition?' <em style="color:var(--gris)">(proposé)</em>':""}</label>`).join("");
 return `<div class="q" id="b${q.id}"><div class="portee">${esc(q.portee)}</div><p>${esc(q.texte)}</p>${radios}
 <label><input type="radio" name="${q.id}" value="__autre">Autre réponse</label>
 <input type="text" id="t${q.id}" placeholder="Précisez" hidden aria-label="Autre réponse">
 ${q.sources&&q.sources.length?`<div class="src">${esc(q.sources.slice(0,3).map(s=>s.doc+" "+s.loc).join(" ; "))}</div>`:""}</div>`}).join("");
function maj(){
 const n=Object.keys(rep).length, t=R.questions.length;
 document.getElementById("qtitre").textContent=`Questions — ${n} / ${t}`;
 document.getElementById("barre").style.width=(t?100*n/t:100)+"%";
 document.getElementById("valider").disabled=n<t;
}
zq.addEventListener("change",e=>{
 const q=e.target.name; if(!q)return;
 const txt=document.getElementById("t"+q);
 txt.hidden=e.target.value!=="__autre";
 if(e.target.value==="__autre"){ if(txt.value.trim())rep[q]=txt.value.trim(); else delete rep[q]; txt.focus(); }
 else rep[q]=e.target.value;
 document.getElementById("b"+q).classList.toggle("fait",q in rep); maj();
});
zq.addEventListener("input",e=>{
 if(e.target.type!=="text")return; const q=e.target.id.slice(1);
 if(e.target.value.trim())rep[q]=e.target.value.trim(); else delete rep[q];
 document.getElementById("b"+q).classList.toggle("fait",q in rep); maj();
});
document.getElementById("valider").onclick=()=>{
 const out={projet:Object.fromEntries(Object.entries(R.projet).map(([k,c])=>[k,c.valeur])),
  ugs:R.ugs.map(u=>({ug_code:u.ug_code,libelle:u.libelle.valeur,type_erc:u.type_erc.valeur,zone_sig:u.zone_sig.valeur,surface_ha:u.surface_ha.valeur})),
  actions:R.actions.map(a=>({code:a.code,intitule:a.intitule.valeur,nature:a.nature.valeur,ugs:a.ugs.valeur,cible:a.cible.valeur})),
  reponses:R.questions.map(q=>({id:q.id,portee:q.portee,question:q.texte,reponse:rep[q.id]})),origine:"user"};
 const s=document.getElementById("sortie"); s.hidden=false;
 document.getElementById("json").textContent=JSON.stringify(out,null,1); s.scrollIntoView({behavior:"smooth"});
};
maj();
</script></body></html>"""


def rendre(sortie: dict) -> str:
    titre = (sortie["referentiel"]["projet"].get("nom") or {}).get("valeur", "projet")
    donnees = json.dumps({k: sortie[k] for k in ("referentiel", "documents", "selection_pdf")},
                         ensure_ascii=False).replace("</", "<\\/")
    return GABARIT.replace("__TITRE__", titre).replace("__DONNEES__", donnees)


if __name__ == "__main__":
    s = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(rendre(s))
