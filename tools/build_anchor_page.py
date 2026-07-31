"""The anchor bank, ranked, editable. Cut what is not good enough, promote what is.

The curator: "I keep seeing a lot of the same examples... If we do a video idea about scheming, it should
show the best scheming, not just some one-off example." So: every anchor, grouped by category, ranked
best-first, with controls to cut or move one. Decisions post back and are applied to the bank, so the
generator sees the curation rather than my guess at it.
"""
import json, os, re, html
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root, not tools/
OUT = os.path.expanduser("~/Downloads/anchors.html")
E = lambda s: html.escape(s or "")

src = json.load(open(os.path.join(ROOT, "backend", "sources.json")))
rows = []
for s in src:
    t = (s.get("shows") or "").strip()
    if not t or not s.get("id"):
        continue
    e = s["esc"] if isinstance(s.get("esc"), int) else 5
    g = s["grab"] if isinstance(s.get("grab"), int) else None
    rank = min(e, g if g is not None else min(e, 6)) + (s.get("bump") or 0)
    rows.append({"id": s["id"], "who": str(s.get("who") or ""), "year": str(s.get("year") or ""),
                 "url": str(s.get("url") or ""),
                 "text": t, "esc": e, "grab": g, "rank": rank,
                 "cat": str(s.get("cat") or "other"), "cut": bool(s.get("cut")),
                 "aism": bool(re.search(r"aisafetymemes|ai safety memes",
                                        str(s.get("who") or "") + str(s.get("url") or ""), re.I))})

by = defaultdict(list)
for r in rows:
    by[r["cat"]].append(r)
for k in by:
    by[k].sort(key=lambda r: (-r["rank"], -(r["grab"] or 0)))

CATS = sorted(by.keys(), key=lambda k: -max(r["rank"] for r in by[k]))
SHOW = 40          # per category, best first; the tail is where the weak ones hide


def row_html(r):
    band = "t10" if r["rank"] >= 9 else ("t8" if r["rank"] >= 7 else ("t6" if r["rank"] >= 5 else "t1"))
    return ("<li class='row%s' data-id='%s' draggable='true'>"
            "<span class='grip'>⠿</span>"
            "<span class='rk %s'>%d</span>"
            "<span class='sc'><i>event</i>%s<i>telling</i>%s</span>"
            "<span class='meta'>%s%s%s</span>"
            "<span class='tx'>%s</span>"
            "<span class='act'><button class='up' title='more important'>▲</button>"
            "<button class='dn' title='less important'>▼</button>"
            "<button class='x' title='never use this one'>✕</button></span></li>"
            % (" cut" if r["cut"] else "", E(r["id"]), band, r["rank"], r["esc"],
               r["grab"] if r["grab"] is not None else "&ndash;",
               E(r["year"]), " &middot; " if r["who"] else "",
               ("<a class='src' href='%s' target='_blank' rel='noopener' title='open the source'>%s ↗</a>"
                % (E(r["url"]), E(r["who"][:26]))) if r["url"] else E(r["who"][:26]),
               E(r["text"][:300])))


def cat_html(cat):
    items = by[cat]
    top = "".join(row_html(r) for r in items[:SHOW])
    tail = "".join(row_html(r) for r in items[-8:]) if len(items) > SHOW else ""
    unscored = sum(1 for r in items if r["grab"] is None)
    return ("<section data-cat='%s'><h2>%s <span class='n'>%d anchors, %d never scored for how they "
            "read</span></h2><ol class='rank'>%s</ol>%s</section>"
            % (E(cat), E(cat.replace("-", " ")), len(items), unscored, top,
               ("<div class='floor'>weakest in this category</div><ol class='rank dim'>%s</ol>" % tail)
               if tail else ""))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Anchor bank</title><style>
:root{--bg:#0e1116;--card:#161b22;--edge:#232a33;--tx:#e6edf3;--mut:#9aa7b4;--grn:#6fd39a;--red:#ff8b7d;--amb:#e0b874}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:26px 20px 120px}
.wrap{max-width:1320px;margin:0 auto}h1{font-size:24px;margin:0 0 6px}
.lead{color:var(--mut);font-size:14.5px;margin:0 0 14px;max-width:900px}
.how{background:#12171e;border:1px solid var(--edge);border-radius:10px;padding:12px 15px;color:var(--mut);font-size:13.4px;margin:0 0 18px}
.how b{color:var(--tx)}
h2{font-size:15.5px;margin:26px 0 9px;border-bottom:1px solid var(--edge);padding-bottom:7px;text-transform:capitalize}
h2 .n{color:var(--mut);font-size:11.5px;font-weight:400;margin-left:8px;text-transform:none}
ol.rank{list-style:none;margin:0;padding:0}
ol.rank.dim li{opacity:.5}
li.row{display:flex;gap:9px;align-items:flex-start;background:var(--card);border:1px solid var(--edge);border-radius:9px;padding:8px 11px;margin:0 0 6px}
li.row.cut{opacity:.32;text-decoration:line-through}
li.row.dragging{opacity:.4}
.grip{color:#4d5867;cursor:grab;font-size:13px;padding-top:3px}
.rk{flex:0 0 auto;width:25px;height:25px;border-radius:6px;font-size:12.5px;font-weight:700;display:flex;align-items:center;justify-content:center}
.t10{background:#3a1f1c;color:#ff9d92}.t8{background:#3a2f18;color:var(--amb)}.t6{background:#1c2b22;color:var(--grn)}.t1{background:#1b2027;color:var(--mut)}
.sc{flex:0 0 104px;font-size:10px;color:#6b7885;padding-top:5px}
.sc i{font-style:normal;margin-right:3px}
.meta{flex:0 0 172px;color:var(--mut);font-size:11.5px;padding-top:4px}
li.row.saved{outline:2px solid #4d9c5a;transition:outline .2s}
a.src{color:#7fb2e5;text-decoration:none}a.src:hover{color:#a9cdf3;text-decoration:underline}
.tx{flex:1;font-size:13.4px;color:#cdd6df}
.act{flex:0 0 auto;display:flex;gap:3px}
.act button{font:inherit;font-size:11px;border:1px solid #2c3540;background:#1a212a;color:#b9c4cf;border-radius:5px;padding:3px 7px;cursor:pointer}
.act .x:hover{background:#4a2320;color:#ffc0b6;border-color:#6b3229}
#bar{position:fixed;left:0;right:0;bottom:0;background:#12171ef2;border-top:1px solid var(--edge);padding:11px 20px;backdrop-filter:blur(8px)}
#bar .in{max-width:1320px;margin:0 auto;display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--mut)}
#bar b{color:var(--tx)}
#bar button{font:inherit;font-size:13px;border:1px solid #33404e;background:#1b222b;color:#dbe3ea;border-radius:7px;padding:7px 13px;cursor:pointer}
</style></head><body><div class="wrap">
<h1>Anchor bank</h1>
<p class="lead">Every real incident the generator can build on, grouped by category and ranked best-first. Two scores: <b>event</b> is how far the AI went, <b>telling</b> is whether the sentence would stop a normal person scrolling. The rank is the lower of the two, which is why a huge event described badly sits low.</p>
<div class="how"><b>Curate it.</b> <b>✕</b> means never use this one. <b>▲ ▼</b> move it up or down the ranking. Drag by the ⠿ handle to move something a long way. Every change sends to me immediately and gets applied to the bank, so the generator uses your ordering rather than my guess at it. The bottom of each category is shown separately, because that is where the weak ones hide.</div>
__BODY__
</div>
<div id="bar"><div class="in">
  <span><b id="ncut">0</b> cut</span><span><b id="nmoved">0</b> moved</span>
  <button id="copy">Copy decisions</button><button id="resend">Re-send all</button>
  <span id="whoami"></span>
  <span style="margin-left:auto">changes save locally and send as you make them</span>
</div></div>
<script>
const API="https://videos-similar-api-production.up.railway.app/event";
const KEY="anchordecisions.v1";
let dec=JSON.parse(localStorage.getItem(KEY)||"{}");
const $=s=>document.querySelector(s);
function save(){localStorage.setItem(KEY,JSON.stringify(dec));
  $("#ncut").textContent=Object.values(dec).filter(d=>d.cut).length;
  $("#nmoved").textContent=Object.values(dec).filter(d=>d.bump).length;}
const WHO=(new URLSearchParams(location.search).get("u")||localStorage.getItem("anchoruser")||"").trim();
if(WHO)localStorage.setItem("anchoruser",WHO);
function send(id,d){fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},keepalive:true,
  body:JSON.stringify({t:"anchorvote",id:id,cut:!!d.cut,bump:d.bump||0,who:WHO||"unknown",ts:new Date().toISOString()})}).catch(()=>{});}
function resort(){
  // A REFRESH USED TO UNDO EVERY MOVE. apply() adjusted the rank NUMBER on the row and stopped
  // there, so the row stayed exactly where the static HTML had written it and only its number
  // changed. Re-sorting each category by the effective rank is what makes a move survive reload.
  document.querySelectorAll("ol.rank").forEach(ol=>{
    const rows=Array.prototype.slice.call(ol.children).filter(li=>li.classList.contains("row"));
    // stable sort, so anchors on the same rank keep the order the page was built with
    rows.sort((a,b)=>(parseInt(b.querySelector(".rk").textContent,10)||0)
                     -(parseInt(a.querySelector(".rk").textContent,10)||0));
    rows.forEach(li=>ol.appendChild(li));
  });
}
function apply(){
  for(const [id,d] of Object.entries(dec)){
    const li=document.querySelector('[data-id="'+CSS.escape(id)+'"]'); if(!li)continue;
    if(d.cut)li.classList.add("cut");
    if(d.bump){const rk=li.querySelector(".rk");rk.textContent=(parseInt(rk.textContent,10)+d.bump);}
  }
  resort();
  save();
}
document.addEventListener("click",e=>{
  if(e.target.closest&&e.target.closest("a.src"))return;   // opening the source is not a vote
  const b=e.target.closest&&e.target.closest("button"); if(!b)return;
  const li=b.closest("li.row"); if(!li)return;
  const id=li.dataset.id; dec[id]=dec[id]||{cut:false,bump:0};
  if(b.classList.contains("x")){dec[id].cut=!dec[id].cut;li.classList.toggle("cut");}
  if(b.classList.contains("up")){dec[id].bump=(dec[id].bump||0)+1;const rk=li.querySelector(".rk");rk.textContent=parseInt(rk.textContent,10)+1;}
  if(b.classList.contains("dn")){dec[id].bump=(dec[id].bump||0)-1;const rk=li.querySelector(".rk");rk.textContent=parseInt(rk.textContent,10)-1;}
  if(b.classList.contains("up")||b.classList.contains("dn")){
    resort(); li.scrollIntoView({block:"center"});   // follow the row you just moved
  }
  save(); send(id,dec[id]);
});
let dragging=null;
document.addEventListener("dragstart",e=>{
  if(e.target.closest&&e.target.closest("a.src")){e.preventDefault();return;}
  const li=e.target.closest&&e.target.closest("li.row");
  if(!li)return;dragging=li;li.classList.add("dragging");});
function record(li){
  // RECORD ON dragend, NOT ONLY ON drop. dragover already moved the row on screen, but the vote
  // was saved in the drop handler, and drop never fires when you release over a heading or the
  // gap between two lists. The row slid into place and nothing was ever sent: the move looked
  // saved and vanished on refresh. dragend always fires, so the recording lives here now.
  const prev=li.previousElementSibling, next=li.nextElementSibling;
  const nrank=(el)=>el&&el.classList.contains("row")?parseInt(el.querySelector(".rk").textContent,10):null;
  const a=nrank(prev), b=nrank(next);
  const target = a!==null&&b!==null ? Math.round((a+b)/2) : (a!==null?a:(b!==null?b:null));
  if(target===null)return;
  const rk=li.querySelector(".rk"); const was=parseInt(rk.textContent,10);
  if(target===was)return;                       // dropped back where it started
  const id=li.dataset.id; dec[id]=dec[id]||{cut:false,bump:0};
  dec[id].bump=(dec[id].bump||0)+(target-was); rk.textContent=target;
  li.classList.add("saved"); setTimeout(()=>li.classList.remove("saved"),900);
  save(); send(id,dec[id]);
}
document.addEventListener("dragend",()=>{
  if(dragging){dragging.classList.remove("dragging"); record(dragging);}
  dragging=null;});
document.addEventListener("dragover",e=>{
  const li=e.target.closest&&e.target.closest("li.row");
  if(!li||!dragging||li===dragging)return; e.preventDefault();
  const r=li.getBoundingClientRect();
  li.parentNode.insertBefore(dragging,(e.clientY-r.top)>r.height/2?li.nextSibling:li);
});
document.addEventListener("drop",e=>{
  if(!dragging)return; e.preventDefault();
  const li=dragging; dragging=null; li.classList.remove("dragging"); record(li);
});
$("#copy").onclick=()=>navigator.clipboard.writeText(JSON.stringify(dec,null,1))
  .then(()=>alert("Copied decisions for "+Object.keys(dec).length+" anchors."));
$("#resend").onclick=async()=>{let ok=0;for(const [id,d] of Object.entries(dec)){try{await fetch(API,{method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify({t:"anchorvote",id:id,cut:!!d.cut,bump:d.bump||0,who:WHO||"unknown"})});ok++;}catch(e){}}
  alert("Re-sent "+ok+" of "+Object.keys(dec).length);};
$("#whoami").textContent = WHO ? ("curating as " + WHO) : "add ?u=yourname to the URL so your picks are attributed";
apply();
</script></body></html>"""

body = "".join(cat_html(c) for c in CATS)
page = PAGE.replace("__BODY__", body)

import subprocess, tempfile
js = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
    fh.write(js); p = fh.name
r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
if r.returncode != 0:
    raise SystemExit("REFUSING TO WRITE: page JS does not parse\n" + r.stderr)
open(OUT, "w").write(page)
print("js ok | wrote %s | %d anchors across %d categories" % (OUT, len(rows), len(CATS)))
