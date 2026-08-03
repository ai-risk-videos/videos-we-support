"""The highlight-and-label widget, shared by every page that shows him sentences.

He labels by SELECTING a span inside a sentence, not by clicking whole sentences: "maybe i can
highlight the parts that suck to make it even more precise?". The verdict alone turned out to be
thin data, so this version also takes a REASON, because two sentences can both be "bad" for
opposite causes and the fix differs: "when i highlight, pop out a little text area so i can type
e.g. 'confusing' 'boring' 'low stakes' etc".

Reasons are stored on the label as `why` and posted with the event, so a future detector can be
fitted per reason instead of against one undifferentiated "bad" pile.

Use: CSS in <style>, HTML anywhere in <body>, JS(storage_key) in <script>. The host page may define
window.onLabelChange to update its own counters; the widget calls it if present.
"""

# quick chips first, because typing a reason every time is friction he will stop paying
REASONS = ["confusing", "boring", "low stakes", "abstract", "passive", "too long", "redundant", "clever"]

CSS = """
mark.g{background:#16301f;color:#8fe3ad;border-bottom:2px solid #4d9c5a;border-radius:2px;cursor:pointer}
mark.b{background:#4a2320;color:#ffc0b6;border-bottom:2px solid #b5544c;border-radius:2px;cursor:pointer}
mark[data-why]{border-bottom-style:dashed}
#pop{position:absolute;display:none;z-index:60;background:#1b222b;border:1px solid #33404e;
  border-radius:10px;padding:9px;box-shadow:0 8px 26px rgba(0,0,0,.6);width:288px}
#pop .row{display:flex;gap:6px;margin-bottom:7px}
#pop button{flex:1;background:#26303b;color:#e8eaed;border:1px solid #3a4653;border-radius:7px;
  padding:6px 0;font-size:13px;cursor:pointer;font-weight:600}
#pop button.bd:hover{background:#4a2320;border-color:#b5544c}
#pop button.gd:hover{background:#16301f;border-color:#4d9c5a}
#pop .chips{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:7px}
#pop .chip{background:#222a34;border:1px solid #333f4c;color:#b9c0cc;border-radius:20px;
  padding:2px 9px;font-size:11.5px;cursor:pointer}
#pop .chip:hover,#pop .chip.on{background:#2d6cdf;border-color:#2d6cdf;color:#fff}
#pop textarea{width:100%;box-sizing:border-box;background:#141a21;color:#e8eaed;border:1px solid #33404e;
  border-radius:7px;padding:6px 8px;font-size:12.5px;font-family:inherit;resize:vertical;min-height:34px}
#pop .hint{color:#7a8290;font-size:11px;margin-top:5px;line-height:1.4}
#pop .sel{color:#8b93a1;font-size:11px;margin-bottom:6px;max-height:32px;overflow:hidden;font-style:italic}
"""

HTML = ("<div id='pop'>"
        "<div class='sel'></div>"
        "<div class='chips'>%s</div>"
        "<textarea placeholder='why? (optional, plain words)'></textarea>"
        "<div class='row'><button class='bd'>Bad</button><button class='gd'>Good</button></div>"
        "<div class='hint'>Pick a chip or type a reason, then Bad / Good. "
        "<b>b</b> and <b>g</b> also work. Click a highlight to remove it.</div>"
        "</div>") % "".join("<span class='chip'>%s</span>" % r for r in REASONS)


def JS(storage_key):
    """The widget script. storage_key namespaces localStorage per page."""
    return """
const LAPI="https://videos-similar-api-production.up.railway.app/event";
const LKEY=%r;
let labels=JSON.parse(localStorage.getItem(LKEY)||"[]");
const _pop=()=>document.getElementById("pop");
function lsave(){localStorage.setItem(LKEY,JSON.stringify(labels));
  if(window.onLabelChange)window.onLabelChange(labels);}
function _why(){
  const p=_pop();
  const chips=[...p.querySelectorAll(".chip.on")].map(c=>c.textContent);
  const typed=p.querySelector("textarea").value.trim();
  return [...chips,...(typed?[typed]:[])].join("; ");
}
function _clearPop(){const p=_pop();p.style.display="none";
  p.querySelectorAll(".chip.on").forEach(c=>c.classList.remove("on"));
  p.querySelector("textarea").value="";}
function _asEl(n){return !n?null:(n.nodeType===1?n:n.parentElement);}
function hostOf(sel){
  // Which sentence does this selection belong to? anchorNode alone is not enough: selecting a whole
  // sentence often anchors on the whitespace text node BETWEEN two spans, which has no .sent
  // ancestor, so the popup used to vanish and the drag looked like it did nothing at all.
  const r=sel.rangeCount?sel.getRangeAt(0):null;
  const cands=[sel.anchorNode,sel.focusNode,r&&r.startContainer,r&&r.endContainer,
               r&&r.commonAncestorContainer];
  for(const n of cands){
    const el=_asEl(n); const h=el&&el.closest?el.closest(".sent"):null;
    if(h)return h;
  }
  // selection spans several sentences: attribute it to the first one it touches
  const el=_asEl(r&&r.commonAncestorContainer);
  return el&&el.querySelector?el.querySelector(".sent"):null;
}
let pending=null;
document.addEventListener("mouseup",e=>{
  if(e.target&&e.target.closest&&e.target.closest("#pop"))return;   // interacting with the popup
  const sel=window.getSelection(); const txt=(sel&&sel.toString()||"").trim();
  if(!txt){_clearPop();pending=null;return;}
  const host=hostOf(sel);
  if(!host){_clearPop();return;}
  pending={range:sel.getRangeAt(0).cloneRange(),text:txt,sid:host.dataset.id||""};
  const r=sel.getRangeAt(0).getBoundingClientRect(); const p=_pop();
  p.querySelector(".sel").textContent='"'+txt.slice(0,90)+(txt.length>90?'..."':'"');
  p.style.display="block";
  // keep it on screen when the selection sits near the right edge or the bottom
  const w=288, x=Math.min(window.scrollX+r.left, window.scrollX+window.innerWidth-w-14);
  p.style.left=Math.max(8,x)+"px"; p.style.top=(window.scrollY+r.bottom+7)+"px";
  p.querySelector("textarea").focus({preventScroll:true});
});
function wrapSelection(range,m){
  const r=range.cloneRange();
  // find the sentence this selection belongs to and never let the mark escape it
  const startEl=r.startContainer.nodeType===1?r.startContainer:r.startContainer.parentElement;
  const host=startEl&&startEl.closest?startEl.closest(".sent"):null;
  if(host){
    if(!host.contains(r.startContainer))r.setStart(host,0);
    if(!host.contains(r.endContainer))r.setEnd(host,host.childNodes.length);
  }
  if(r.collapsed)return false;
  try{r.surroundContents(m);return true;}catch(e){}
  try{m.appendChild(r.extractContents());r.insertNode(m);
      if(m.parentNode)m.parentNode.normalize();return true;}catch(e){}
  return false;
}
function mark(v){
  if(!pending)return;
  const why=_why();
  const m=document.createElement("mark"); m.className=(v==="bad"?"b":"g");
  const id=Date.now()+"-"+Math.round(Math.random()*1e6); m.dataset.lid=id;
  if(why)m.dataset.why=why, m.title=why;
  // surroundContents() throws the moment a selection is not inside one text node, which is what
  // happens on the most natural gesture there is: dragging across a whole sentence and catching
  // the trailing space or the next span. Clamp the range to the sentence, then extract-and-insert,
  // which handles a selection spanning several nodes. Only give up if BOTH routes fail.
  if(!wrapSelection(pending.range, m)){alert("Could not mark that. Try selecting a bit less.");return;}
  const rec={id:id,sid:pending.sid,text:pending.text,verdict:v,why:why,ts:new Date().toISOString()};
  labels.push(rec);
  fetch(LAPI,{method:"POST",headers:{"Content-Type":"application/json"},keepalive:true,
    body:JSON.stringify({t:"sentlabel",verdict:v,text:rec.text,sid:rec.sid,why:why,ts:rec.ts})}).catch(()=>{});
  lsave(); window.getSelection().removeAllRanges(); _clearPop(); pending=null;
}
document.addEventListener("click",e=>{
  const t=e.target; if(!t||!t.closest)return;
  const chip=t.closest("#pop .chip");
  if(chip){chip.classList.toggle("on");return;}            // chips are multi-select
  if(t.closest("#pop button.bd")){mark("bad");return;}
  if(t.closest("#pop button.gd")){mark("good");return;}
  const m=t.closest("mark");
  if(m&&m.dataset.lid){
    labels=labels.filter(l=>l.id!==m.dataset.lid);
    const tn=document.createTextNode(m.textContent); m.replaceWith(tn);
    if(tn.parentNode)tn.parentNode.normalize(); lsave();
  }
});
document.addEventListener("keydown",e=>{
  if(!pending)return;
  // b / g must not fire while he is typing the reason
  if(e.target&&(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")){
    if(e.key==="Enter"&&(e.metaKey||e.ctrlKey)){e.preventDefault();mark("bad");}
    return;
  }
  if(e.key==="b"){e.preventDefault();mark("bad");}
  if(e.key==="g"){e.preventDefault();mark("good");}
  if(e.key==="Escape"){_clearPop();pending=null;}
});
function labelCount(){return labels.length;}
function labelJSON(){return JSON.stringify(labels,null,1);}
async function labelResend(){let ok=0;for(const l of labels){try{await fetch(LAPI,{method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify({t:"sentlabel",verdict:l.verdict,
  text:l.text,sid:l.sid,why:l.why||"",ts:l.ts})});ok++;}catch(e){}}
  alert("Re-sent "+ok+" of "+labels.length);}
if(window.onLabelChange)window.onLabelChange(labels);
""" % storage_key
