#!/usr/bin/env python3
"""Build the LEADS triage page from leads_ranked.json.

A lead = one punchy factual sentence (a documented incident/stat/headline), scored by the
harvest-and-vote workflow on hook x fresh x real. The curator reviews them ranked, thumbs-down the
ones that should not be near the top; the survivors become the seed pool for video-idea generation.
Leads are embedded inline so the page needs no server. Kills persist in localStorage.
"""
import json, os, html, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "leads_ranked.json")
# the tool is now the front door: write it to index.html (root) AND leads.html (back-compat links)
OUT = os.path.join(HERE, "species-web-deploy", "index.html")
OUT2 = os.path.join(HERE, "species-web-deploy", "leads.html")

leads = json.load(open(SRC, encoding="utf-8"))
# keep the fields the page needs, cap length of any runaway entry
clean = []
for x in leads:
    l = (x.get("l") or "").strip()
    if not l:
        continue
    clean.append({
        "l": l, "url": x.get("url", ""), "who": x.get("who", ""),
        "y": str(x.get("y", "")), "cat": x.get("cat", ""),
        "h": x.get("hook", 0), "f": x.get("fresh", 0), "r": x.get("real", 0),
        "s": x.get("s", x.get("score", 0)), "x": bool(x.get("x")), "dirs": x.get("dirs",[]),
    })
DATA = json.dumps(clean, ensure_ascii=False)
PV = datetime.datetime.utcnow().strftime("%m%d.%H%M")

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Leads — Species</title>
<script src="https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/9.22.0/firebase-firestore-compat.js"></script>
<style>
:root{--bg:#0b0a0c;--ink:#ece8f0;--mut:#8a8290;--surf:#141218;--line:#2a2630;--red:#e20020;--red2:#ff5a6e;--gold:#ffcf4d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:30px 18px 120px}
h1{font-size:26px;margin:0 0 4px;font-weight:800}.dot{color:var(--red)}
.sub{color:var(--mut);font-size:14px;margin-bottom:14px}
.bar{position:sticky;top:0;z-index:5;background:rgba(11,10,12,.94);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
  display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 -18px 12px;padding:11px 18px}
.count{font-weight:700;font-size:14px}.count b{color:var(--gold)}
.bar button{font:inherit;font-size:12.5px;background:var(--surf);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 12px;cursor:pointer}
.bar button:hover{border-color:var(--red2)}
.filters{margin-left:auto;display:flex;gap:6px;font-size:12px;color:var(--mut);align-items:center}
.lead{display:flex;gap:12px;padding:14px 0;border-bottom:1px solid var(--line)}
.lead.killed{opacity:.32}
.rank{font-size:12px;color:var(--mut);min-width:30px;padding-top:3px;font-variant-numeric:tabular-nums}
.body{flex:1}
.txt{font-size:16.5px;line-height:1.5}
.killed .txt{text-decoration:line-through}
.turns{margin-top:5px}
.turn{color:#9a93a2;font-size:14px;line-height:1.5;padding-left:11px;border-left:2px solid #33303a;margin:3px 0}
.subtxt{color:#cfc9d6;font-size:15px;line-height:1.6;margin:8px 0 2px}
.meta{margin-top:5px;font-size:12px;color:var(--mut);display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.packbtn{font:inherit;font-size:11.5px;background:#161c14;color:#9ac47f;border:1px solid #33402e;border-radius:6px;padding:2px 9px;cursor:pointer}
.packbtn:hover{border-color:#a9d99a;color:#c5e6b3}
.packbtn:disabled{opacity:.6;cursor:default}
.brief{display:none;margin-top:10px;padding:16px 18px;background:var(--surf);border:1px solid #33402e;border-radius:10px;color:#cfced3;font-size:14px;line-height:1.62}
.brief h4{color:#a9d99a;font-size:13px;letter-spacing:.06em;text-transform:uppercase;margin:16px 0 6px}
.brief h4.bh3{font-size:12.5px;color:#8fc47a;text-transform:none;letter-spacing:.02em;margin:15px 0 5px}
.brief p{margin:6px 0}
.brief .bli{margin:4px 0 4px 4px;padding-left:15px;position:relative}
.brief .bli:before{content:"•";position:absolute;left:2px;color:#6f8a5f}
.brief .bli.sub{margin-left:18px;font-size:13px;color:#b6b4bb}
.bhead{font-weight:700;color:#a9d99a;margin-bottom:4px}
.bsaved{font-size:11px;color:#6f6875;font-weight:400;margin-left:8px;text-transform:none;letter-spacing:0}
.bnum{margin:5px 0 5px 8px}
.btab{border-collapse:collapse;margin:8px 0;width:100%}
.btab td{border:1px solid #2c3529;padding:5px 8px;font-size:13px;vertical-align:top}
.cnum{font-size:11px;vertical-align:super;text-decoration:none;color:#d98a8a;margin:0 1px}
.link{background:none;border:none;padding:0;color:#857d8a;font-size:12.5px;cursor:pointer}
.link:hover{color:var(--ink)}
.bdl{margin-top:10px}
.simmsg{color:#857d8a;font-size:13px;padding:4px 0;display:block}
.simerr{color:var(--red2)}
.ptick{padding:6px 0}
.ptbar{height:6px;background:#221f28;border-radius:99px;overflow:hidden;margin-bottom:7px}
.ptbar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#9ac47f,#ffcf4d);border-radius:99px;transition:width .9s linear}
.ptmeta{font-size:13px;color:var(--ink);font-weight:600}
.ptmeta .ptsec{color:var(--gold);font-variant-numeric:tabular-nums}
.pthint{font-size:12px;color:var(--mut);margin-top:2px}
.fmtpick{position:fixed;left:50%;top:38%;transform:translate(-50%,-50%);z-index:60;background:#17141a;border:1px solid #3a3340;border-radius:14px;padding:20px 22px;max-width:420px;box-shadow:0 18px 60px rgba(0,0,0,.6)}
.fmth{font-weight:700;margin-bottom:12px;font-size:14.5px}
.fmtc{display:inline-block;margin:4px 6px 4px 0;padding:8px 13px;background:#221d26;border:1px solid #3a3340;border-radius:8px;color:#e6e2e9;cursor:pointer;font-size:13.5px}
.fmtc:hover{border-color:var(--red2);color:#fff}
.meta a{color:#8ab4d8;text-decoration:none;border-bottom:1px dotted #3a4a5a}
.sc{font-variant-numeric:tabular-nums}
.acts{display:flex;flex-direction:column;gap:4px}
.acts button{font:inherit;font-size:15px;line-height:1;background:none;border:1px solid var(--line);border-radius:7px;padding:6px 9px;cursor:pointer;color:var(--mut)}
.kill.on{background:#2a0e12;border-color:var(--red);color:var(--red2)}
.star.on{background:#2a2410;border-color:var(--gold);color:var(--gold)}
.acts button:hover{border-color:var(--red2)}
.pagev{color:#57515c;font-size:11px}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#1c1922;border:1px solid var(--line);color:var(--ink);padding:10px 16px;border-radius:10px;font-size:13.5px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:9}
.toast.show{opacity:1}
.chanbox{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 14px}
.chantitle{font-weight:700;font-size:15px;margin-bottom:3px}
.chansub{color:var(--mut);font-size:12.5px;margin-bottom:10px}
.chanrow{display:flex;gap:8px;flex-wrap:wrap}
#curl{flex:1;min-width:200px;background:#0e0d10;border:1px solid var(--line);border-radius:8px;color:var(--ink);font:inherit;font-size:14px;padding:9px 12px}
#curl:focus{outline:none;border-color:var(--red2)}
#curl{width:100%}
.cmsg{color:var(--mut);font-size:12.5px;margin-top:8px}
.cmsg.err{color:var(--red2)}
.steps{display:flex;gap:7px;align-items:center;flex-wrap:wrap;font-size:11.5px;color:var(--mut);margin-bottom:11px}
.steps .stepnow{color:var(--ink);font-weight:700}
.steps .steparr{opacity:.5}
.chanalt{margin-top:9px}
.linklike{background:none;border:none;color:var(--mut);font:inherit;font-size:12.5px;text-decoration:underline;text-underline-offset:2px;cursor:pointer;padding:2px 0}
.linklike:hover{color:var(--ink)}
.genrow{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.genbtn{flex:1;min-width:210px;display:flex;flex-direction:column;gap:3px;text-align:left;font:inherit;font-size:14px;font-weight:700;background:var(--surf);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:12px 15px;cursor:pointer}
.genbtn:hover{border-color:var(--mut)}
.genbtn.primary{background:var(--red);border-color:var(--red);color:#fff}
.genbtn.primary:hover{background:#c50019}
.genbtn:disabled{opacity:.55;cursor:default}
.gensub{font-size:11.5px;font-weight:400;opacity:.85}
.genbtn.primary .gensub{color:#ffdfe3}
.regenrow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 14px;padding:10px 12px;background:var(--surf);border:1px solid var(--line);border-radius:9px;font-size:12.5px;color:var(--mut)}
.regenbtn{font:inherit;font-size:12.5px;font-weight:700;background:none;border:1px solid var(--line);color:var(--ink);border-radius:7px;padding:6px 11px;cursor:pointer}
.regenbtn:hover{border-color:var(--red2)}
.regenbtn:disabled{opacity:.5;cursor:default}
.homerow{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:6px}
.navbtn.ghost{background:none;color:var(--mut)}
.navbtn.ghost:hover{color:var(--ink);border-color:var(--line)}
.poolback{font:inherit;font-size:12.5px;background:none;border:1px solid var(--line);color:var(--ink);border-radius:7px;padding:7px 12px;cursor:pointer}
.homehint{color:var(--mut);font-size:13px;padding:20px 2px;line-height:1.6}
.tbanner{display:none;align-items:center;gap:10px;flex-wrap:wrap;background:#161c14;border:1px solid #33402e;border-radius:10px;padding:9px 14px;margin:0 0 14px;font-size:13px;color:#c5e6b3}
.tbanner b{color:#fff}
.tbanner button{font:inherit;font-size:12px;background:none;border:1px solid #3a5230;color:#9ac47f;border-radius:6px;padding:4px 10px;cursor:pointer}
.tbanner button:hover{border-color:#a9d99a;color:#c5e6b3}
.hype{margin-top:12px;border-top:1px solid #2c3529;padding-top:10px}
.hype summary{cursor:pointer;color:#9ac47f;font-size:13px;font-weight:600;list-style:none}
.hype summary::-webkit-details-marker{display:none}
.hype summary:before{content:"\\25B8  "}
.hype[open] summary:before{content:"\\25BE  "}
.hypebody{font-size:13.5px;color:#c9c2ce;line-height:1.6;margin-top:8px}
.hypebody ul{margin:6px 0;padding-left:18px}
.hypebody li{margin:4px 0}
.hypebody p{margin:7px 0}
.scriptbtn{font:inherit;font-size:11.5px;background:#1a1622;color:#b79ae0;border:1px solid #3a2f4e;border-radius:6px;padding:2px 9px;cursor:pointer}
.scriptbtn:hover{border-color:#c5adf0;color:#d8c6f5}
.scriptbtn:disabled{opacity:.6;cursor:default}
.scriptbox{display:none;margin-top:10px;padding:16px 18px;background:var(--surf);border:1px solid #3a2f4e;border-radius:10px;color:#cfced3;font-size:14.5px;line-height:1.62}
.scriptbox h4{color:#c5adf0;font-size:13px;letter-spacing:.04em;text-transform:uppercase;margin:14px 0 6px}
.scriptbox p{margin:8px 0}
.scripthead{font-weight:700;color:#c5adf0;margin-bottom:6px}
.scripthedge{font-size:13px;color:var(--mut);font-style:italic;border-left:2px solid #33303a;padding-left:11px;margin:0 0 12px;line-height:1.5}
.scriptbox .cue{color:#8a7fa8;font-size:12.5px}
/* prominent share panel (admin, after tailoring) */
.sharepanel{flex-basis:100%;margin-top:9px;background:#10140e;border:1px solid #33402e;border-radius:8px;padding:11px 13px}
.sharehead{font-size:12.5px;color:#9ac47f;font-weight:700;margin-bottom:7px}
.sharerow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sharelink{flex:1;min-width:220px;background:#0e0d10;border:1px solid var(--line);border-radius:7px;color:#c9c2ce;font:inherit;font-size:12.5px;padding:8px 10px}
.sharebtn{font:inherit;font-size:12.5px;font-weight:700;background:#9ac47f;color:#0b0a0c;border:none;border-radius:7px;padding:8px 13px;cursor:pointer}
.sharebtn:hover{background:#b4d99a}
.sharebtn.ghost{background:none;border:1px solid #3a5230;color:#9ac47f}
.sharebtn.ghost:hover{border-color:#9ac47f;background:none}
/* clean CREATOR view: opened via ?c=@handle, admin chrome hidden */
.chead{display:none}
.chead h1{font-size:25px;margin:0 0 6px;font-weight:800}
.chead .csub2{color:var(--mut);font-size:14.5px;line-height:1.55;max-width:72ch}
.creator #adminhome,.creator #poolbar,.creator .bar,.creator #tbanner,.creator .acts,.creator .sc{display:none!important}
.creator .chead{display:block;margin-bottom:22px}
.creator .lead{padding:16px 0}
/* publish / curate / dashboard */
.pubbtn{font:inherit;font-size:12px;font-weight:700;background:#9ac47f;color:#0b0a0c;border:none;border-radius:7px;padding:8px 13px;cursor:pointer}
.pubbtn:hover{background:#b4d99a}
.navbtn{font:inherit;font-size:13px;background:var(--surf);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:9px 13px;cursor:pointer}
.navbtn:hover{border-color:var(--red2)}
.curbar{position:sticky;top:0;z-index:6;background:rgba(11,10,12,.96);border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 -18px 14px;padding:12px 18px}
.curtitle{font-weight:700;font-size:15px;flex:1;min-width:150px}
.saveind{font-size:12px;padding:3px 9px;border-radius:99px;white-space:nowrap}
.saveind.ok{color:#9ac47f;background:rgba(154,196,127,.12)}
.saveind.saving{color:var(--gold);background:rgba(255,207,77,.12)}
.saveind.err{color:var(--red2);background:rgba(255,90,110,.12)}
#curnote{width:100%;background:#0e0d10;border:1px solid var(--line);border-radius:8px;color:var(--ink);font:inherit;font-size:13.5px;padding:9px 12px;resize:vertical;min-height:38px;margin:0 0 14px}
.editnote{color:var(--mut);font-size:12.5px;margin:0 0 10px}
.curstep{font-size:12.5px;color:#a9d99a;margin:0 0 8px}
.rnote{font-size:12.5px;color:#9ac47f;background:rgba(154,196,127,.10);border:1px solid #33402e;border-radius:8px;padding:7px 11px;margin:0 0 10px}
.rnote.rwarn{color:var(--gold);background:rgba(255,207,77,.10);border-color:#4a4327}
.addrow{display:flex;gap:8px;align-items:flex-start;margin:14px 0 6px}
.addrow textarea{flex:1;font:inherit;font-size:14px;background:var(--surf);color:var(--ink);border:1px dashed var(--line);border-radius:9px;padding:9px 11px;resize:vertical;min-height:44px}
.addbtn{font:inherit;font-size:13px;font-weight:700;background:var(--surf);color:#9ac47f;border:1px solid #33402e;border-radius:9px;padding:9px 14px;cursor:pointer;white-space:nowrap}
.addbtn:hover{border-color:#9ac47f}
.rejbox{margin:16px 0 8px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.02)}
.rejbox>summary{cursor:pointer;padding:10px 13px;font-size:13px;font-weight:700;color:var(--mut);list-style:none;display:flex;align-items:center;gap:10px}
.rejbox>summary::-webkit-details-marker{display:none}
.rejhint{font-weight:400;font-size:12px;color:#6f6a77}
.rejcopy{margin-left:auto;font:inherit;font-size:12px;font-weight:700;background:none;border:1px solid var(--line);color:var(--ink);border-radius:7px;padding:5px 10px;cursor:pointer}
.rejcopy:hover{border-color:var(--gold)}
#rejlist{padding:2px 13px 12px}
.rejrow{display:flex;gap:10px;align-items:center;padding:6px 0;border-top:1px solid var(--line)}
.rejtxt{flex:1;font-size:13px;color:#8f8a97;line-height:1.4}
.rejrestore{font:inherit;font-size:12px;background:none;border:1px solid var(--line);color:var(--mut);border-radius:7px;padding:3px 9px;cursor:pointer;white-space:nowrap}
.rejrestore:hover{border-color:#9ac47f;color:#9ac47f}
.ccard{position:relative;padding:13px 15px;border:1px solid var(--line);border-radius:10px;margin:0 0 11px;background:var(--surf)}
.ccard .ctxt{font-size:16px;line-height:1.5;outline:none}
.ccard .ctxt[contenteditable]:focus,.ccard .cturn[contenteditable]:focus{background:#161320;border-radius:4px;box-shadow:0 0 0 2px #33303a}
.ccard .cturn{color:#9a93a2;font-size:14px;line-height:1.5;padding-left:11px;border-left:2px solid #33303a;margin:5px 0;outline:none}
.ccard .cctl{display:flex;gap:6px;margin-top:9px;align-items:center}
.ccard .cctl button{font:inherit;font-size:12px;background:none;border:1px solid var(--line);border-radius:6px;padding:4px 9px;cursor:pointer;color:var(--mut)}
.ccard .cctl button:hover{border-color:var(--red2);color:var(--red2)}
.ccard .cnum2{color:#57515c;font-size:12px;font-weight:700;margin-right:6px}
.dashrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 0;border-bottom:1px solid var(--line)}
.dashrow .dchan{font-weight:700;flex:1;min-width:150px}
.dashrow .dmeta{color:var(--mut);font-size:12px}
.dashrow button,.dashrow a{font:inherit;font-size:12px;background:var(--surf);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;color:var(--ink);text-decoration:none}
.dashrow button:hover,.dashrow a:hover{border-color:var(--red2)}
.pagenote{background:#141b12;border:1px solid #33402e;border-radius:10px;padding:13px 16px;margin:0 0 18px;color:#cfd8c8;font-size:14.5px;line-height:1.55;white-space:pre-wrap}
.morebtn{display:block;margin:22px auto 8px;font:inherit;font-size:14px;font-weight:700;background:var(--surf);color:#9ac47f;border:1px solid #33402e;border-radius:9px;padding:12px 20px;cursor:pointer}
.morebtn:hover{border-color:#9ac47f}
.editpagebtn{position:fixed;top:12px;right:12px;z-index:60;font:inherit;font-size:12.5px;font-weight:800;background:var(--gold);color:#0b0a0c;border:none;border-radius:20px;padding:9px 16px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.5)}
.editpagebtn:hover{filter:brightness(1.08)}
@media(max-width:640px){.editpagebtn{top:8px;right:8px;font-size:12px;padding:8px 13px}}
.premakebtn{position:fixed;top:12px;right:150px;z-index:60;font:inherit;font-size:12.5px;font-weight:800;background:var(--surf);color:var(--gold);border:1px solid var(--gold);border-radius:20px;padding:9px 15px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.5)}
.premakebtn:hover{filter:brightness(1.12)}
.premakebtn:disabled{opacity:.65;cursor:default}
@media(max-width:640px){.premakebtn{top:44px;right:8px;font-size:12px;padding:8px 12px}}
.premakebar{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;width:min(440px,92vw);background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:0 8px 28px rgba(0,0,0,.55)}
.premakebar .pmtxt{font-size:12.5px;color:var(--ink);margin-bottom:8px}
.premakebar .pmtrack{height:7px;background:rgba(255,255,255,.08);border-radius:99px;overflow:hidden}
.premakebar .pmfill{height:100%;width:0;background:var(--gold);border-radius:99px;transition:width .4s ease}
.premakebar .pmstop{margin-top:9px;font:inherit;font-size:11.5px;font-weight:700;background:none;border:1px solid var(--line);color:var(--mut);border-radius:7px;padding:4px 10px;cursor:pointer}
.moredivide{border-top:1px solid var(--line);margin:24px 0 8px;padding-top:14px;color:var(--mut);font-size:13px;font-weight:700}
</style></head><body>
<div class="wrap">
<div class="chead" id="chead"></div>
<div id="adminhome">
<h1 id="mh1">Video ideas for creators<span class="dot">.</span></h1>
<div class="sub">Paste a creator's channel and get a tailored list of AI-risk video ideas. Trim and tweak the list, publish it, then send them a private link. That's the whole job.</div>
<div class="chanbox">
 <div class="chantitle">Make a page for a channel</div>
 <div class="steps"><span class="stepnow">1 · Paste the channel</span><span class="steparr">→</span><span>2 · Review &amp; tidy</span><span class="steparr">→</span><span>3 · Publish &amp; send the link</span></div>
 <div class="chanrow">
  <input id="curl" type="text" placeholder="Paste a YouTube channel, e.g. youtube.com/@kurzgesagt" autocomplete="off" spellcheck="false">
 </div>
 <div class="genrow">
  <button id="cgen" class="genbtn primary">✍️ Write fresh ideas for them<span class="gensub">brand-new, bespoke to their channel · about 40s · recommended</span></button>
  <button id="cgo" class="genbtn">📚 Pull from our idea library<span class="gensub">faster · ranks our vetted pool for them</span></button>
 </div>
 <div class="cmsg" id="cmsg"></div>
</div>
<div class="homerow">
 <button id="cpages" class="navbtn">📄 My published pages</button>
 <button id="browseall" class="navbtn ghost" title="Browse and triage the full library of ideas (not needed to make a channel page)">Browse all ideas</button>
 <a href="https://videos-similar-api-production.up.railway.app/pipeline" target="_blank" rel="noopener" class="navbtn ghost" style="text-decoration:none" title="How it works behind the scenes: the flow, the live prompts, the knobs — for getting more eyes on the process">⚙ How it works</a>
</div>
</div>
<div class="tbanner" id="tbanner"></div>
<div class="bar" id="poolbar" style="display:none">
 <button id="poolback" class="poolback">← Back to start</button>
 <span class="count" id="count"></span>
 <button id="starred">★ Show starred</button>
 <button id="hidekilled">Hide killed</button>
 <button id="copykept">⧉ Copy kept</button>
 <button id="export">Export</button>
 <button id="import">Import</button>
 <input id="importfile" type="file" accept="application/json,.json" hidden>
 <button id="reset">Reset</button>
 <span class="filters"><span class="pagev">v __PV__</span></span>
</div>
<div id="list"></div>
</div>
<div class="toast" id="toast"></div>
<script>
const LEADS=__DATA__;
// ---- Firestore: durable per-channel published pages (same project as the ideas app) ----
let DB=null;
try{firebase.initializeApp({apiKey:"AIzaSyDlxzdAUzSWwBLY5dkkGrJz_70oGJmv94o",authDomain:"thumbnail-tester-b1746.firebaseapp.com",projectId:"thumbnail-tester-b1746",storageBucket:"thumbnail-tester-b1746.firebasestorage.app",messagingSenderId:"310329953022",appId:"1:310329953022:web:8c626451eb761aa7b7f3af"});DB=firebase.firestore();}catch(e){console.error("Firestore init failed",e);}
const PAGES_COL="creator_pages";
// ---- channel tailoring (/tailor): re-rank the pool for one creator; profile also shapes packs ----
const TAILOR_API="https://videos-similar-api-production.up.railway.app/tailor";
const CUSTOM_API="https://videos-similar-api-production.up.railway.app/custom"; // old "Videos We Support" idea generation
const BUILD_V="__PV__"; // tailor-cache is keyed to this: LEADS indices shift when the page is rebuilt
let channelProfile="", channelName="", channelHandle="", tailored=false, fullView=false, tailorOrder=null;
let currentPageId=""; // set on a saved ?p= creator page; lets packs/scripts save ONTO that page so the creator sees them instantly
let researchNote=""; // one-shot line shown in the editor after a fresh generation: how many transcripts were read (so it is VISIBLE the tool used them)
// an "idea" is either a leads-style lead {l,dirs} or an old-style idea {title,summary}; these read both
function ideaTitle(x){return (x&&(x.title||x.l))||"";}
function ideaSummary(x){return (x&&(x.summary||((x.dirs||[]).join(" "))))||"";}
let creatorView=false; // true when opened via ?c=@handle → clean creator-facing page (admin chrome hidden)
let creatorNote=""; // note of the currently open ?p= page, so admin "+ get more" can persist the note too
let genBusy=false; // guards against two overlapping generate requests (double-click / Enter spam)
let inCurate=false; // true while editing an unpublished draft — guards against silently losing it
// If a draft is open and the user does anything that would blow it away, confirm first.
function confirmLeaveDraft(){if(!inCurate)return true;const ok=confirm("You have an unpublished draft. Leave it and lose your edits?");if(ok)inCurate=false;return ok;}
// ---- in-app history: without this the browser Back button leaves the whole site on the first press,
// because every screen change here is just DOM show/hide. We give the browser one history entry per
// screen (home / your pages / pool / editor / a creator page) so Back and Forward walk the screens. ----
let _navState=null, _popping=false;
function _urlFor(s){const b=location.pathname;
 if(s.v==="page")return b+"?p="+encodeURIComponent(s.id);
 if(s.v==="dash")return b+"?v=pages";
 if(s.v==="pool")return b+"?v=pool";
 if(s.v==="edit")return b+"?v=edit"+(s.h?("&h="+encodeURIComponent(s.h)):"");
 return b;}
function _screenFromLocation(){const q=new URLSearchParams(location.search);
 const p=(q.get("p")||"").trim(),v=(q.get("v")||"").trim();
 if(p)return{v:"page",id:p};
 if(v==="pages")return{v:"dash"};
 if(v==="pool")return{v:"pool"};
 if(v==="edit")return{v:"edit",h:(q.get("h")||"").trim()};
 return{v:"home"};}
function _pushScreen(s){ // called at the top of each screen; a no-op during pop/initial render, and dedups
 if(_popping){_navState=s;return;}
 if(_navState&&JSON.stringify(_navState)===JSON.stringify(s))return;
 _navState=s;try{history.pushState(s,"",_urlFor(s));}catch(e){}}
async function _renderScreen(s){s=s||{v:"home"};
 // any admin screen must drop read-only creator mode first — else Forward/Back into the editor would
 // hit startCurate's creatorView early-return and render the read-only board instead of the editor.
 if(s.v!=="page"){creatorView=false;document.body.classList.remove("creator");}
 if(s.v==="page"){await renderCreatorPage(s.id);}
 else if(s.v==="dash"){await showDashboard();}
 else if(s.v==="pool"){enterPool();}
 else if(s.v==="edit"){
  if(inCurate)return; // already in the editor (pushState round-trip) — don't reload over live edits
  let doc=null;try{doc=await loadPageDoc(s.h);}catch(e){}
  if(doc&&Array.isArray(doc.ideas)&&doc.ideas.length)startCurate(doc.ideas,doc.handle||s.h,doc.channel||doc.handle||s.h,doc.note||"",doc.profile||"",doc.style||"leads",doc.rejected||[]);
  else showHome();}
 else{showHome();}}
window.addEventListener("popstate",async function(ev){
 const s=(ev&&ev.state)||_screenFromLocation();
 // leaving the editor via Back: flush the autosave first so nothing is dropped (no scary dialog — autosave
 // means the draft is already safe and shows up under "My published pages"), then render the target.
 if(inCurate&&s.v!=="edit"){try{await flushSave();}catch(e){}inCurate=false;}
 _navState=s;_popping=true; // set _navState BEFORE the async render so a late push (edit reload) dedups even if a second pop flips _popping
 try{await _renderScreen(s);}finally{_popping=false;}
});
// The calm starting screen: no wall of ideas, no toolbar — just the one job (make a page for a channel).
function showHome(){_pushScreen({v:"home"});inCurate=false;creatorView=false;document.body.classList.remove("creator");
 currentPageId=""; // left the saved page → don't let stray pack/script saves target it
 ["editpagebtn","premakebtn","premakebar"].forEach(c=>{const e=document.querySelector("."+c);if(e)e.remove();});
 const tb=$("#tbanner");if(tb)tb.style.display="none";
 const pb=$("#poolbar");if(pb)pb.style.display="none";
 const ah=$("#adminhome");if(ah)ah.style.display="";
 const ch=$("#chead");if(ch)ch.innerHTML="";
 const l=$("#list");if(l)l.innerHTML='<div class="homehint">Paste a channel above to get started. Your published pages live under <b>📄 My published pages</b>. There is nothing else you need to do here.</div>';
 const m=$("#cmsg");if(m){m.textContent="";m.className="cmsg";}
 const c=$("#curl");if(c)c.value="";
}
// admin = this browser has ever used the builder tool (or ?admin in the URL). Lets the admin edit a
// creator's ?p= page in place; a creator opening the same link fresh has no flag and just sees the page.
function isAdmin(){try{return localStorage.getItem("species_admin")==="1";}catch(e){return false;}}
function setAdmin(){try{localStorage.setItem("species_admin","1");}catch(e){}}
// ---- research pack (/brief): same flow as the ideas page, keyed on the lead sentence ----
const BRIEF_API="https://videos-similar-api-production.up.railway.app/brief";
const _BK="species_lead_briefs";
function _bload(){try{return JSON.parse(localStorage.getItem(_BK)||"{}")}catch(e){return{}}}
function _bsave(t,md){try{const m=_bload();m[t]={md:md,ts:Date.now()};const ks=Object.keys(m);if(ks.length>40){ks.sort((a,b)=>(m[a].ts||0)-(m[b].ts||0));ks.slice(0,ks.length-40).forEach(k=>delete m[k]);}localStorage.setItem(_BK,JSON.stringify(m));}catch(e){}}
const briefCache={};
try{const _pm=_bload();for(const k in _pm)briefCache[k]=_pm[k].md;}catch(e){}
function _safeUrl(u){return /^(https?:|mailto:)/i.test((u||"").trim());} // block javascript:/data: hrefs
// Live progress so a creator on a slow generate knows it's working and doesn't click away.
// Renders an easing bar (asymptotes to ~93%, never "done" until stop()) + an elapsed counter.
function progressTicker(el,estSec,label){
 if(!el)return function(){};
 const est=estSec||60;
 el.innerHTML='<div class="ptick"><div class="ptbar"><i></i></div><div class="ptmeta"><span class="ptlabel">'+esc(label||"Working")+'</span> · <span class="ptsec">0s</span> <span style="color:var(--mut);font-weight:400">(usually '+Math.round(est/2)+"-"+est+'s)</span></div><div class="pthint">Hang tight and keep this open, it is working.</div></div>';
 const bar=el.querySelector(".ptbar>i"), sec=el.querySelector(".ptsec"), hint=el.querySelector(".pthint");
 const t0=Date.now();
 const iv=setInterval(function(){
  const s=Math.round((Date.now()-t0)/1000);
  if(sec)sec.textContent=s+"s";
  if(bar)bar.style.width=(93*(1-Math.exp(-s/est))).toFixed(1)+"%";
  if(hint&&s>est)hint.textContent="Taking a little longer than usual, still working, hang on.";
 },1000);
 return function(){clearInterval(iv);if(bar)bar.style.width="100%";};
}
function mdLite(md){ // headers, tables, bold, citation links — enough for the research pack
 const esc2=x=>x.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
 const lines=(md||"").split("\\n");let out="",inTable=false;
 for(const ln of lines){
  if(/^\s*\|/.test(ln)){
   if(/^\s*\|[\s:-]+\|/.test(ln))continue;
   const cells=ln.split("|").slice(1,-1).map(c=>esc2(c.trim()).replace(/\[([^\]]+)\]\(([^)]+)\)/g,(mm,tx,u)=>!_safeUrl(u)?tx:(/^\d{1,2}$/.test(tx)?'<a class="cnum" href="'+u+'" target="_blank" rel="noopener">['+tx+']</a>':'<a href="'+u+'" target="_blank" rel="noopener">'+tx+'</a>')).replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>"));
   if(!inTable){out+="<table class='btab'>";inTable=true;}
   out+="<tr>"+cells.map(c=>"<td>"+c+"</td>").join("")+"</tr>";continue;
  } else if(inTable){out+="</table>";inTable=false;}
  let h=esc2(ln);
  h=h.replace(/\[([^\]]+)\]\(([^)]+)\)/g,(mm,tx,u)=>!_safeUrl(u)?tx:(/^\d{1,2}$/.test(tx)?'<a class="cnum" href="'+u+'" target="_blank" rel="noopener">['+tx+']</a>':'<a href="'+u+'" target="_blank" rel="noopener">'+tx+'</a>')).replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>");
  if(/^### /.test(ln))out+="<h4 class='bh3'>"+h.slice(4)+"</h4>";
  else if(/^## /.test(ln))out+="<h4>"+h.slice(3)+"</h4>";
  else if(/^# /.test(ln))out+="<h4>"+h.slice(2)+"</h4>";
  else if(/^\s*[-*] /.test(ln))out+="<div class='bli'>"+h.replace(/^\s*[-*] /,"")+"</div>";
  else if(/^\s{2,}[-*] /.test(ln))out+="<div class='bli sub'>"+h.replace(/^\s*[-*] /,"")+"</div>";
  else if(/^\d+\. /.test(ln))out+="<div class='bnum'>"+h+"</div>";
  else if(ln.trim()==="")out+="";
  else out+="<p>"+h+"</p>";
 }
 if(inTable)out+="</table>";
 return out;
}
const FORMATS=["Explainer / educational","Storytelling / documentary","Commentary / reaction","Interview / podcast","True crime / mystery","Finance / data","Maker / hands on","Other"];
function getFormat(cb){ // one-tap, remembered across leads and the ideas page
 let f=null;try{f=localStorage.getItem("species_format")}catch(e){}
 if(f){cb(f);return;}
 let m=document.querySelector(".fmtpick");if(m)m.remove();
 m=document.createElement("div");m.className="fmtpick";
 m.innerHTML='<div class="fmth">Quick one: what kind of videos do you make? (shapes your research pack)</div>'+FORMATS.map(x=>'<button class="fmtc">'+esc(x)+'</button>').join("");
 document.body.appendChild(m);
 m.querySelectorAll(".fmtc").forEach(b=>{b.onclick=()=>{const v=b.textContent;try{localStorage.setItem("species_format",v)}catch(e){};m.remove();cb(v);};});
}
async function loadBrief(d,lead,btn){
 const box=d.querySelector(".brief"), t=ideaTitle(lead);
 if(box.style.display==="block"){box.style.display="none";return;} // toggle closed
 if(briefCache[t]){renderBrief(box,briefCache[t],t);return;}
 // pre-made / already-generated on this page → show instantly, no format prompt, no wait
 if(currentPageId){const ob=btn.textContent;btn.disabled=true;btn.textContent="…";
  const saved=await loadArtifact(currentPageId,"brief",t);btn.disabled=false;btn.textContent=ob;
  if(saved){briefCache[t]=saved;_bsave(t,saved);renderBrief(box,saved,t);return;}}
 loadBrief2(d,box,lead,"",btn); // no format prompt: the server shapes the pack from the transcript-derived channel profile it already has
}
async function loadBrief2(d,box,lead,fmt,btn){
 if(d._briefLoading)return;d._briefLoading=true;
 const t=ideaTitle(lead);
 box.style.display="block";
 box.innerHTML='<div class="ptwrap"></div>';
 const _stop=progressTicker(box.querySelector(".ptwrap"),75,"Building your research pack");
 const old=btn.textContent;btn.textContent="…";btn.disabled=true;
 try{
  const bctrl=new AbortController();const bto=setTimeout(()=>bctrl.abort(),180000);
  const summary=ideaSummary(lead);
  const payload={title:t,summary:summary};if(fmt)payload.format=fmt;
  if(channelProfile&&channelProfile.length>80)payload.profile=channelProfile; // auto-shape to the tailored channel
  const r=await fetch(BRIEF_API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:bctrl.signal});
  clearTimeout(bto);
  if(r.status===429){box.innerHTML='<span class="simmsg simerr">Busy right now — try again in a minute.</span>';return;}
  const j=await r.json();
  if(j&&j.brief){briefCache[t]=j.brief;_bsave(t,j.brief);renderBrief(box,j.brief,t);if(currentPageId)saveArtifact(currentPageId,"brief",t,j.brief).then(ok=>{if(!ok&&isAdmin())toast("Heads up: couldn't save this pack to the page");});}
  else box.innerHTML='<span class="simmsg simerr">Could not build it just now — try again in a moment.</span>';
 }catch(e){box.innerHTML='<span class="simmsg simerr">Took too long — try again in a moment.</span>';}
 finally{_stop();btn.textContent=old;btn.disabled=false;d._briefLoading=false;}
}
// Canonical, static rebuttal — identical in every pack, collapsed by default, so the case lives
// in ONE place on demand instead of eating two paragraphs of every generated pack.
const HYPE_HTML=`<details class="hype"><summary>Isn't this just hype to sell product?</summary><div class="hypebody"><p>No, and the objection has the story backwards. Every other technology runs one way: the inventor swears it is safe, and critics accuse them of hiding the danger to make a sale. AI is the exact inversion. Here the people who built it are the ones warning it could kill you, and the comeback is that they must be <b>exaggerating</b> the danger to look cool or cash in.</p><p>Sit with how strange that is. Nobody sells a product by warning it might murder your family. Telling the world your own invention could end it is the worst sales pitch ever written, which is why the loudest voices are working against their own interest:</p><ul><li>Geoffrey Hinton <b>quit Google</b>, a Nobel laureate walking away from his job, so he could warn people freely.</li><li>Yoshua Bengio, the most cited computer scientist alive, stepped back to do the same.</li><li>Hundreds of researchers, Nobel laureates, and national security figures across the political spectrum have signed statements that the risk is real.</li><li>A warning from inside an AI company is an admission against interest, the one kind of corporate statement skeptics should actually believe.</li></ul><p>For this to be marketing, rival companies, independent academics, and government scientists would all have to secretly coordinate to talk down their own industry. That is not skepticism. It is a conspiracy theory.</p></div></details>`;
function renderBrief(box,md,title){
 box.style.display="block";
 box.innerHTML='<div class="bhead">Research pack <span class="bsaved">saved</span></div>'+mdLite(md)+HYPE_HTML+'<button class="link bdl">⬇ Download this pack</button>';
 box.querySelector(".bdl").onclick=()=>{
  const doc='<html><head><meta charset="utf-8"><title>Research pack</title><style>body{font:15px/1.6 -apple-system,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#1a1a1a}h4{margin:22px 0 6px}table{border-collapse:collapse;margin:10px 0}td{border:1px solid #ddd;padding:6px 9px;font-size:13.5px}a{color:#2563eb}</style></head><body><h1 style="font-size:19px">Research pack</h1><p style="color:#555">'+esc(title)+'</p>'+mdLite(md)+'</body></html>';
  const blob=new Blob([doc],{type:"text/html"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="research-pack-"+title.replace(/[^a-z0-9]+/gi,"-").toLowerCase().slice(0,50)+".html";
  document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),1000);toast("Research pack downloaded");
 };
}
// ---- sample script (/script): same flow as the research pack; best when a channel is tailored
// (the transcript-derived profile makes the script read in that creator's actual voice) ----
const SCRIPT_API="https://videos-similar-api-production.up.railway.app/script";
const _SK2="species_lead_scripts";
function _sload(){try{return JSON.parse(localStorage.getItem(_SK2)||"{}")}catch(e){return{}}}
function _ssave(t,md){try{const m=_sload();m[t]={md:md,ts:Date.now()};const ks=Object.keys(m);if(ks.length>40){ks.sort((a,b)=>(m[a].ts||0)-(m[b].ts||0));ks.slice(0,ks.length-40).forEach(k=>delete m[k]);}localStorage.setItem(_SK2,JSON.stringify(m));}catch(e){}}
const scriptCache={};
try{const _sm=_sload();for(const k in _sm)scriptCache[k]=_sm[k].md;}catch(e){}
async function loadScript(d,lead,btn){
 const box=d.querySelector(".scriptbox"), t=ideaTitle(lead);
 if(box.style.display==="block"){box.style.display="none";return;} // toggle closed
 if(scriptCache[t]){renderScript(box,scriptCache[t],t);return;}
 // pre-made / already-generated on this page → show instantly, no format prompt, no wait
 if(currentPageId){const ob=btn.textContent;btn.disabled=true;btn.textContent="…";
  const saved=await loadArtifact(currentPageId,"script",t);btn.disabled=false;btn.textContent=ob;
  if(saved){scriptCache[t]=saved;_ssave(t,saved);renderScript(box,saved,t);return;}}
 loadScript2(d,box,lead,"",btn); // no format prompt: the server writes in the creator's voice from the transcript-derived profile
}
async function loadScript2(d,box,lead,fmt,btn){
 if(d._scriptLoading)return;d._scriptLoading=true;
 const t=ideaTitle(lead);
 box.style.display="block";
 box.innerHTML='<div class="ptwrap"></div>';
 const _stop=progressTicker(box.querySelector(".ptwrap"),85,"Writing a sample script"+(channelName?(" in "+channelName+"'s voice"):""));
 const old=btn.textContent;btn.textContent="…";btn.disabled=true;
 try{
  const sc=new AbortController();const sto=setTimeout(()=>sc.abort(),180000);
  const summary=ideaSummary(lead);
  const payload={title:t,summary:summary};if(fmt)payload.format=fmt;
  if(channelProfile&&channelProfile.length>80)payload.profile=channelProfile; // write in the tailored channel's voice
  if(channelHandle)payload.channelUrl=cleanChanUrl(channelHandle); // lets the server pull transcripts + build the voice bible
  const r=await fetch(SCRIPT_API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:sc.signal});
  clearTimeout(sto);
  if(r.status===429){box.innerHTML='<span class="simmsg simerr">Busy right now — try again in a minute.</span>';return;}
  const j=await r.json();
  if(j&&j.script){scriptCache[t]=j.script;_ssave(t,j.script);renderScript(box,j.script,t);if(currentPageId)saveArtifact(currentPageId,"script",t,j.script).then(ok=>{if(!ok&&isAdmin())toast("Heads up: couldn't save this script to the page");});}
  else box.innerHTML='<span class="simmsg simerr">Could not write it just now — try again in a moment.</span>';
 }catch(e){box.innerHTML='<span class="simmsg simerr">Took too long — try again in a moment.</span>';}
 finally{_stop();btn.textContent=old;btn.disabled=false;d._scriptLoading=false;}
}
function renderScript(box,md,title){
 box.style.display="block";
 // render bracketed [CUE] stage directions in a muted color so the spoken words stand out
 // style [CUE] stage directions muted, but NOT numeric citation links ([1],[2]...) which mdLite already turned into <a>
 const cued=mdLite(md).replace(/\[([^\]0-9][^\]]*)\]/g,'<span class="cue">[$1]</span>');
 box.innerHTML='<div class="scripthead">Sample script'+(channelName?(" · "+esc(channelName)+"'s voice"):"")+'</div>'+
  '<div class="scripthedge">This is just to give an indication of what a video like this might look like. It is not a script we are asking you to follow, just make it your own.</div>'+
  cued+'<button class="link bdl">⬇ Download this script</button>';
 box.querySelector(".bdl").onclick=()=>{
  const doc='<html><head><meta charset="utf-8"><title>Sample script</title><style>body{font:15px/1.7 -apple-system,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;color:#1a1a1a}h4{margin:22px 0 6px}</style></head><body><h1 style="font-size:19px">Sample script</h1><p style="color:#555">'+esc(title)+'</p><p style="color:#888;font-style:italic;border-left:3px solid #ddd;padding-left:12px">This is just to give an indication of what a video like this might look like. It is not a script we are asking you to follow, just make it your own.</p>'+mdLite(md)+'</body></html>';
  const blob=new Blob([doc],{type:"text/html"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="sample-script-"+title.replace(/[^a-z0-9]+/gi,"-").toLowerCase().slice(0,50)+".html";
  document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),1000);toast("Sample script downloaded");
 };
}
const KK="species_leads_killed_v1", SK="species_leads_starred_v1";
// guarded like every other localStorage read: a throw here (blocked storage / corrupt value)
// runs at module scope and would blank the ENTIRE page, including the creator ?p= view.
function _loadSet(k){try{const v=JSON.parse(localStorage.getItem(k)||"[]");return new Set(Array.isArray(v)?v:[]);}catch(e){return new Set();}}
const killed=_loadSet(KK);
const starred=_loadSet(SK);
let onlyStar=false, hideKilled=false;
let viewOrder=LEADS.map((_,i)=>i); // indices into LEADS in display order (tailoring rewrites this)
const $=s=>document.querySelector(s);
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;")}
function key(l){return l.slice(0,80)}
let _t;function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("show");clearTimeout(_t);_t=setTimeout(()=>t.classList.remove("show"),1600)}
function save(){
 try{
  const k=JSON.stringify([...killed]),s=JSON.stringify([...starred]);
  localStorage.setItem(KK,k);localStorage.setItem(SK,s);
  if(localStorage.getItem(KK)!==k||localStorage.getItem(SK)!==s)throw new Error("readback mismatch");
  return true;
 }catch(e){console.error("Could not save lead triage",e);toast("⚠ Change could not be saved");return false}
}
function decisionRows(set){return LEADS.map((x,i)=>({key:key(x.l),rank:i+1,lead:x.l,url:x.url||"",who:x.who||"",year:x.y||"",category:x.cat||""})).filter(x=>set.has(x.key))}
function exportDecisions(){
 const payload={format:"species-lead-decisions",version:1,exportedAt:new Date().toISOString(),totalLeads:LEADS.length,killed:decisionRows(killed),starred:decisionRows(starred)};
 const blob=new Blob([JSON.stringify(payload,null,2)],{type:"application/json"});
 const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="species-lead-decisions-"+new Date().toISOString().slice(0,10)+".json";a.click();
 setTimeout(()=>URL.revokeObjectURL(a.href),1000);toast("Exported "+killed.size+" killed + "+starred.size+" starred")
}
function keysFromImport(rows){return (Array.isArray(rows)?rows:[]).map(x=>typeof x==="string"?x:(x&&x.key)||"").filter(Boolean)}
async function importDecisions(file){
 try{
  const data=JSON.parse(await file.text());
  if(!data||data.format!=="species-lead-decisions")throw new Error("not a Species decisions file");
  killed.clear();starred.clear();keysFromImport(data.killed).forEach(k=>killed.add(k));keysFromImport(data.starred).forEach(k=>starred.add(k));
  starred.forEach(k=>killed.delete(k));
  if(!save())return;render();toast("Imported "+killed.size+" killed + "+starred.size+" starred")
 }catch(e){console.error("Could not import decisions",e);toast("⚠ That decisions file could not be imported")}
}
function render(){
 const list=$("#list");list.innerHTML="";
 let shown=0;
 viewOrder.forEach((idx,pos)=>{
  const x=LEADS[idx];if(!x)return;
  const k=key(x.l), isK=killed.has(k), isS=starred.has(k);
  if(onlyStar&&!isS)return;
  if(hideKilled&&isK)return;
  shown++;
  const d=document.createElement("div");d.className="lead"+(isK?" killed":"");
  d.innerHTML='<div class="rank">'+(pos+1)+'</div>'+
   '<div class="body"><div class="txt">'+esc(x.l)+'</div>'+((x.dirs&&x.dirs.length)?'<div class="turns">'+x.dirs.map(t=>'<div class="turn">'+esc(t)+'</div>').join("")+'</div>':'')+
   '<div class="meta">'+(x.who?'<span>'+esc(x.who)+(x.y?" · "+esc(x.y):"")+'</span>':'')+
   (x.url?'<a href="'+esc(x.url)+'" target="_blank" rel="noopener">read more ›</a>':'')+
   '<span class="sc">elo '+x.s+'</span>'+
   '<button class="packbtn">📄 Research pack</button>'+
   '<button class="scriptbtn">🎬 Sample script</button></div>'+
   '<div class="brief"></div><div class="scriptbox"></div></div>'+
   '<div class="acts"><button class="kill'+(isK?' on':'')+'" title="shouldn\\'t be near the top">👎</button>'+
   '<button class="star'+(isS?' on':'')+'" title="standout">★</button></div>';
  d.querySelector(".kill").onclick=()=>{if(killed.has(k)){killed.delete(k)}else{killed.add(k);starred.delete(k)}if(save())render()};
  d.querySelector(".star").onclick=()=>{if(starred.has(k)){starred.delete(k)}else{starred.add(k);killed.delete(k)}if(save())render()};
  d.querySelector(".packbtn").onclick=(e)=>loadBrief(d,x,e.currentTarget);
  d.querySelector(".scriptbtn").onclick=(e)=>loadScript(d,x,e.currentTarget);
  list.appendChild(d);
 });
 const kept=LEADS.length-killed.size;
 $("#count").innerHTML="<b>"+kept+"</b> kept · "+killed.size+" killed · "+starred.size+" starred"+(onlyStar||hideKilled?(" · showing "+shown):"");
}
// ---- channel tailoring: POST top leads to /tailor, reorder the board, remember + share ----
const TCK="species_lead_tailor_v1";
function tload(){try{return JSON.parse(localStorage.getItem(TCK)||"{}")}catch(e){return{}}}
function tsave(o){try{localStorage.setItem(TCK,JSON.stringify(o))}catch(e){}}
function handleFromUrl(u){u=(u||"").trim();const m=u.match(/@[\w.-]+/);if(m)return m[0].toLowerCase();return (u.replace(/^https?:\/\/(www\.)?youtube\.com\//i,"").replace(/[/?#].*$/,"")||u).toLowerCase();}
function cleanChanUrl(u){u=(u||"").trim();if(!u)return"";
 u=u.replace(/^https?:\\/\\//i,"").replace(/\\s+/g,"").replace(/[?#].*$/,""); // drop scheme, spaces, query
 // already a youtube URL (with or without www, with a /@handle, /channel/, /c/, /user/ path)? keep the whole path.
 if(/^(www\\.)?(youtube\\.com|youtu\\.be)\\//i.test(u))return"https://"+u;
 // otherwise it's a bare handle or name → make it a channel URL
 return"https://www.youtube.com/@"+u.replace(/^@+/,"");}
function topLeadsForTailor(){const out=[];for(let i=0;i<LEADS.length&&out.length<120;i++){if(killed.has(key(LEADS[i].l)))continue;out.push({i:i,l:LEADS[i].l});}return out;}
function applyView(){viewOrder=(tailored&&!fullView&&tailorOrder&&tailorOrder.length)?tailorOrder.slice():LEADS.map((_,i)=>i);showBanner();render();}
function setCreatorHeader(name){const c=$("#chead");if(c)c.innerHTML='<h1>Video ideas for '+esc(name)+' we might be interested in supporting<span class="dot">.</span></h1>';}
function showBanner(){
 const b=$("#tbanner");if(!b)return;
 if(creatorView){setCreatorHeader(channelName||(channelHandle||"").replace(/^@/,""));b.style.display="none";return;}
 if(!channelName){b.style.display="none";b.innerHTML="";return;}
 b.style.display="flex";
 b.innerHTML='<span>🎯 Tailored for <b>'+esc(channelName)+'</b>'+((tailored&&!fullView&&tailorOrder)?(' · '+tailorOrder.length+' best-fit leads'):' · showing full pool')+'</span>'+
  '<button class="pubbtn" id="curatebtn">✏️ Curate &amp; publish this list</button>'+
  '<button id="ttoggle">'+(fullView?"Show tailored":"Show full pool")+'</button>'+
  '<button id="tclear">Clear</button>'+
  '<div class="chansub" style="flex-basis:100%;margin-top:2px">Curate the list (remove, edit the wording, reorder), then publish a stable link to send '+esc(channelName)+'.</div>';
 $("#curatebtn").onclick=()=>{const idxs=(tailored&&tailorOrder&&tailorOrder.length)?tailorOrder:LEADS.map((_,i)=>i);const ideas=idxs.slice(0,40).map(i=>LEADS[i]).filter(Boolean);startCurate(ideas,channelHandle,channelName,"",channelProfile);};
 $("#ttoggle").onclick=()=>{fullView=!fullView;applyView();};
 $("#tclear").onclick=()=>{channelProfile="";channelName="";channelHandle="";tailored=false;fullView=false;tailorOrder=null;const m=$("#cmsg");if(m)m.textContent="";applyView();};
}
async function fetchTailor(rawurl,useCache){
 const url=cleanChanUrl(rawurl);if(!url){toast("Paste a channel link");return false;}
 const h=handleFromUrl(url); // stays local until success, so a failed re-tailor can't leave a wrong handle live
 const cache=tload();
 // cache is keyed to this build: LEADS indices shift on rebuild, so a stale-version entry is discarded
 if(useCache&&cache[h]&&cache[h].v===BUILD_V&&Array.isArray(cache[h].order)&&cache[h].order.length){
  const c=cache[h];channelHandle=h;channelProfile=c.profile||"";channelName=c.channel||h;
  tailorOrder=c.order.filter(i=>i>=0&&i<LEADS.length);tailored=true;fullView=false;
  const m0=$("#cmsg");if(m0)m0.textContent="";
  startCurate(tailorOrder.slice(0,40).map(i=>LEADS[i]).filter(Boolean),channelHandle,channelName,"",channelProfile,"leads");
  toast("Ideas ready for "+channelName);return true;
 }
 const btn=$("#cgo"),msg=$("#cmsg"),orig=btn?btn.textContent:"";
 if(btn){btn.disabled=true;btn.textContent="Reading channel…";}
 let _stopT=function(){};
 if(msg){msg.className="cmsg";msg.innerHTML='<div class="ptwrap"></div>';_stopT=progressTicker(msg.querySelector(".ptwrap"),90,"Reading the channel and ranking ideas");}
 try{
  const ctrl=new AbortController();const to=setTimeout(()=>ctrl.abort(),200000);
  const r=await fetch(TAILOR_API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channelUrl:url,leads:topLeadsForTailor()}),signal:ctrl.signal});
  clearTimeout(to);
  if(r.status===429){if(msg){msg.className="cmsg err";msg.textContent="Busy right now — wait a minute and try again.";}return false;}
  const j=await r.json();
  if(j&&Array.isArray(j.order)&&j.order.length){
   channelHandle=h;channelProfile=j.profile||"";channelName=j.channel||h;
   tailorOrder=j.order.filter(i=>i>=0&&i<LEADS.length);tailored=true;fullView=false;
   const cc=tload();cc[h]={profile:channelProfile,channel:channelName,order:tailorOrder,v:BUILD_V,ts:Date.now()};tsave(cc);
   if(msg)msg.textContent="";
   startCurate(tailorOrder.slice(0,40).map(i=>LEADS[i]).filter(Boolean),channelHandle,channelName,"",channelProfile,"leads");
   toast("Ideas ready for "+channelName);return true;
  }
  if(msg){msg.className="cmsg err";msg.textContent=(j&&j.error)?j.error:"Could not tailor that channel. Check the link.";}return false;
 }catch(e){if(msg){msg.className="cmsg err";msg.textContent="Took too long — try again.";}return false;}
 finally{_stopT();if(btn){btn.disabled=false;btn.textContent=orig||"Tailor to this channel";}}
}
// ================= publish / curate / dashboard / creator pages =================
function pageId(h){return (h||"").replace(/^@+/,"").toLowerCase().replace(/[^a-z0-9_-]/g,"")||"page";}
async function savePage(handle,channel,note,ideas,profile,style,rejected){
 if(!DB)throw new Error("cloud unavailable");
 const id=pageId(handle);
 const ref=DB.collection(PAGES_COL).doc(id);
 const base={handle:handle||("@"+id),channel:channel||handle||id,note:note||"",ideas:ideas,profile:profile||"",style:style||"leads",updated:Date.now(),v:BUILD_V};
 if(rejected!==undefined)base.rejected=rejected; // only write the reject pile when the caller manages it; otherwise it is preserved below
 // SAFETY NET: snapshot the PREVIOUS idea set into a version history before overwriting, so no
 // publish / regenerate / autosave can ever permanently destroy ideas. Done inside a TRANSACTION
 // so two tabs (or a flush racing a debounced save) can't clobber each other or lose a version.
 // Snapshots are time-coalesced (rapid autosaves collapse to ~1 per 45s) and the ORIGINAL/oldest
 // version is PINNED so a long editing session can never evict the set you started from.
 try{
  await DB.runTransaction(async function(tx){
   const snap=await tx.get(ref);
   let history=[]; let keepRej=(rejected!==undefined)?rejected:[];
   if(snap.exists){
    const p=snap.data()||{};
    history=Array.isArray(p.history)?p.history.slice():[];
    if(rejected===undefined)keepRej=Array.isArray(p.rejected)?p.rejected:[]; // caller didn't manage it → preserve the existing reject pile (never wipe it via tx.set)
    const prevIdeas=Array.isArray(p.ideas)?p.ideas:[];
    const last=history.length?history[history.length-1]:null;
    const changed=prevIdeas.length && JSON.stringify(prevIdeas)!==JSON.stringify(ideas);
    const distinct=!last||JSON.stringify(last.ideas)!==JSON.stringify(prevIdeas);
    const spaced=!last||((Date.now()-(last.ts||0))>45000);
    if(changed && distinct && spaced){history.push({ideas:prevIdeas,note:p.note||"",style:p.style||"leads",ts:p.updated||Date.now()});}
    if(history.length>12){history=[history[0]].concat(history.slice(history.length-11));} // pin the original + keep the last 11
   }
   tx.set(ref,Object.assign({},base,{history:history,rejected:keepRej}));
  });
 }catch(e){
  // transaction unavailable/offline: merge-set WITHOUT a history key so any existing history is
  // preserved untouched (never clobber history to []); the ideas still save.
  await ref.set(base,{merge:true});
 }
 return id;
}
async function loadPageDoc(id){if(!DB)return null;const s=await DB.collection(PAGES_COL).doc(pageId(id)).get();return s.exists?s.data():null;}
// ---- per-idea artifacts (research packs + sample scripts) saved ONTO the page, in a subcollection
// (creator_pages/<id>/artifacts/<type>__<slug>) so the person we send the link to sees them instantly
// instead of waiting a minute to regenerate. Kept out of the main doc so they never push it toward
// Firestore's 1MB cap. The existing recursive read/write rule already covers this subcollection. ----
function _hash32(s){let h=0x811c9dc5;for(let i=0;i<(s||"").length;i++){h^=s.charCodeAt(i);h=Math.imul(h,0x01000193);}return (h>>>0).toString(36);}
// slug + a hash of the FULL title, so two ideas that share a 90-char prefix (or a non-Latin/emoji
// title that slugifies to nothing) never collide onto the same artifact doc and overwrite each other.
function artKey(t){t=t||"";const base=t.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"").slice(0,90)||"x";return base+"-"+_hash32(t);}
function artRef(pid,type,t){return DB.collection(PAGES_COL).doc(pid).collection("artifacts").doc(type+"__"+artKey(t));}
async function loadArtifact(pid,type,t){if(!DB||!pid)return null;try{const s=await withTimeout(artRef(pid,type,t).get(),8000);return (s&&s.exists&&s.data())?(s.data().md||null):null;}catch(e){return null;}}
async function saveArtifact(pid,type,t,md){if(!DB||!pid||!md)return false;try{await artRef(pid,type,t).set({md:md,title:t,ts:Date.now()});return true;}catch(e){return false;}}
// headless generators (no DOM/ticker) used by the pre-make loop; mirror loadBrief2/loadScript2's payloads
async function _genPack(lead,fmt){
 const t=ideaTitle(lead);const payload={title:t,summary:ideaSummary(lead)};if(fmt)payload.format=fmt;
 if(channelProfile&&channelProfile.length>80)payload.profile=channelProfile;
 const c=new AbortController();const to=setTimeout(()=>c.abort(),180000);
 try{const r=await fetch(BRIEF_API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:c.signal});
  if(!r.ok)return null;const j=await r.json();const md=j&&j.brief;if(md){briefCache[t]=md;_bsave(t,md);}return md||null;}
 catch(e){return null;}finally{clearTimeout(to);}
}
async function _genScript(lead,fmt){
 const t=ideaTitle(lead);const payload={title:t,summary:ideaSummary(lead)};if(fmt)payload.format=fmt;
 if(channelProfile&&channelProfile.length>80)payload.profile=channelProfile;
 if(channelHandle)payload.channelUrl=cleanChanUrl(channelHandle);
 const c=new AbortController();const to=setTimeout(()=>c.abort(),180000);
 try{const r=await fetch(SCRIPT_API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:c.signal});
  if(!r.ok)return null;const j=await r.json();const md=j&&j.script;if(md){scriptCache[t]=md;_ssave(t,md);}return md||null;}
 catch(e){return null;}finally{clearTimeout(to);}
}
function _fmtRem(sec){if(!sec||sec<0)return"a moment";if(sec<90)return Math.max(1,Math.round(sec))+"s";return Math.round(sec/60)+" min";}
// Pre-make the research pack + sample script for EVERY idea and save each onto the page as it lands,
// so the creator opens the link and sees them with no wait. Re-running only fills gaps (skips saved ones).
async function premakeAll(btn,ideas){
 ideas=(ideas||[]).filter(Boolean);
 if(!ideas.length||!currentPageId){toast("Nothing to prepare yet");return;}
 let fmt="";try{fmt=localStorage.getItem("species_format")||"";}catch(e){}
 btn.disabled=true;btn.textContent="Checking what's ready…";
 const checks=[]; // parallel existence check so a big page doesn't stall on 2×N sequential reads
 for(const lead of ideas){const t=ideaTitle(lead);if(!t)continue;
  checks.push(loadArtifact(currentPageId,"brief",t).then(v=>({lead:lead,type:"brief",has:!!v})));
  checks.push(loadArtifact(currentPageId,"script",t).then(v=>({lead:lead,type:"script",has:!!v})));}
 const jobs=(await Promise.all(checks)).filter(c=>!c.has).map(c=>({lead:c.lead,type:c.type}));
 if(!jobs.length){btn.textContent="✓ Everything's ready for the creator";setTimeout(()=>{btn.disabled=false;btn.textContent="⚡ Pre-make packs + scripts";},4000);return;}
 let done=0,failed=0;const total=jobs.length;let t0=Date.now();let stopped=false; // done = CONFIRMED saved; failed = generation or save error
 const old=document.querySelector(".premakebar");if(old)old.remove();
 const bar=document.createElement("div");bar.className="premakebar";
 bar.innerHTML='<div class="pmtxt"></div><div class="pmtrack"><div class="pmfill"></div></div><button class="pmstop">Stop</button>';
 document.body.appendChild(bar);
 const txt=bar.querySelector(".pmtxt"),fill=bar.querySelector(".pmfill");
 bar.querySelector(".pmstop").onclick=()=>{stopped=true;};
 function upd(){const seen=done+failed;fill.style.width=Math.round(seen/total*100)+"%";
  const per=seen?((Date.now()-t0)/seen):0;const rem=per?(per*(total-seen)/1000):0;
  const ff=failed?(" · "+failed+" to retry"):"";
  txt.textContent=stopped?("Stopping after in-flight items… "+done+"/"+total+" saved"+ff):("Preparing packs + scripts · "+done+"/"+total+" saved"+ff+" · about "+_fmtRem(rem)+" left");}
 upd();btn.textContent="Pre-making… (keep this tab open)";
 const CONC=2;let idx=0; // small pool so we don't hammer the API or trip rate limits
 async function worker(){while(idx<jobs.length&&!stopped){const job=jobs[idx++];let okj=false;
  try{const md=job.type==="brief"?await _genPack(job.lead,fmt):await _genScript(job.lead,fmt);
   if(md)okj=await saveArtifact(currentPageId,job.type,ideaTitle(job.lead),md);}catch(e){}
  if(okj)done++;else failed++; // only count a job "done" when the save actually landed — never claim success on a rate-limit/error
  upd();}}
 await Promise.all(Array.from({length:Math.min(CONC,jobs.length)},()=>worker()));
 btn.disabled=false;
 if(stopped){btn.textContent="⚡ Resume pre-making";txt.textContent="Stopped. "+done+" of "+total+" saved"+(failed?" ("+failed+" failed).":".");setTimeout(()=>bar.remove(),7000);}
 else if(failed){btn.textContent="⚡ Retry "+failed+" that failed";txt.textContent=done+" of "+total+" saved. "+failed+" couldn't be made (likely rate-limited) — click Retry in a minute.";toast(failed+" didn't save — click Retry");}
 else{btn.textContent="✓ All packs + scripts ready";txt.textContent="Done — all "+total+" saved. The creator sees them instantly.";toast("Pre-made everything for "+esc(channelName));setTimeout(()=>bar.remove(),9000);}
}
let curateIdeas=[], curateHandle="", curateChannel="", curateProfile="", curateStyle="leads", curateDirty=false, curateSaveT=null, curateRejected=[];
// ---- autosave: the editor persists every change on its own, so you never lose work and never
// have to click a "Save". A debounce coalesces rapid edits; savePage() keeps version history. ----
function _setSaveInd(state){const el=$("#saveind");if(!el)return;
 if(state==="saving"){el.textContent="Saving…";el.className="saveind saving";}
 else if(state==="saved"){el.textContent="All changes saved";el.className="saveind ok";}
 else if(state==="err"){el.textContent="Save failed — will retry";el.className="saveind err";}}
async function doAutoSave(){
 if(!inCurate||!DB||!curateHandle)return;
 const note=(($("#curnote")&&$("#curnote").value)||"").trim();
 const ideas=curateIdeas.filter(x=>ideaTitle(x).trim().length>0||ideaSummary(x).trim().length>0); // keep anything with a title OR body; never drop a short title or a mid-edit blank-title card
 if(!ideas.length)return; // never autosave an empty set — that would erase the page
 _setSaveInd("saving");
 try{await savePage(curateHandle,curateChannel,note,ideas,curateProfile,curateStyle,curateRejected);_setSaveInd("saved");}
 catch(e){_setSaveInd("err");clearTimeout(curateSaveT);curateSaveT=setTimeout(doAutoSave,4000);} // retry on failure
}
function scheduleSave(ms){clearTimeout(curateSaveT);curateSaveT=setTimeout(doAutoSave,(ms==null?1400:ms));if($("#saveind"))_setSaveInd("saving");}
async function flushSave(){clearTimeout(curateSaveT);await doAutoSave();}
// ---- restore a previous version (undo any bad save/regenerate) from the doc's history ----
async function showRestore(){
 await flushSave(); // land any pending edit before we tear down the editor DOM (and stop a queued autosave from firing after)
 const curNote=(($("#curnote")&&$("#curnote").value)||""); // capture BEFORE the innerHTML swap destroys #curnote, so Back doesn't blank the note
 let doc=null;try{doc=await loadPageDoc(curateHandle);}catch(e){}
 const hist=(doc&&Array.isArray(doc.history))?doc.history.slice().reverse():[];
 const cur={ideas:curateIdeas.slice(),note:curNote,style:curateStyle,ts:Date.now(),_current:true};
 const rows=[cur].concat(hist);
 const list=$("#list");
 list.innerHTML='<div class="curbar"><div class="curtitle">↩ Restore a previous version of '+esc(curateChannel)+'</div><button class="navbtn" id="restoreback">‹ Back to editing</button></div>'+
  '<div class="editnote">Your recent versions are saved here. Pick one to bring back; your current version is kept too, so a restore can itself be undone.</div><div id="verlist"></div>';
 $("#restoreback").onclick=()=>startCurate(curateIdeas,curateHandle,curateChannel,curNote,curateProfile,curateStyle);
 $("#verlist").innerHTML=rows.map((v,i)=>{
  const when=v.ts?new Date(v.ts).toLocaleString():"";
  const n=(v.ideas||[]).length;
  const first=n?ideaTitle(v.ideas[0]).slice(0,80):"(empty)";
  return '<div class="dashrow"><span class="dchan">'+(v._current?"Current":"Saved "+esc(when))+'</span><span class="dmeta">'+n+' ideas · '+esc(first)+'…</span>'+(v._current?'':'<button data-ver="'+i+'">Restore this</button>')+'</div>';
 }).join("");
 $("#verlist").querySelectorAll("[data-ver]").forEach(bn=>bn.onclick=()=>{const v=rows[+bn.getAttribute("data-ver")];if(v){startCurate(v.ideas,curateHandle,curateChannel,v.note||"",curateProfile,v.style||curateStyle);toast("Restored a "+((v.ideas||[]).length)+"-idea version");}});
}
function curTitle(){return "✏️ Curating "+curateChannel+" — "+curateIdeas.length+" ideas";}
function startCurate(ideas,handle,channel,note,profile,style,rejected){
 if(creatorView){ // ?c= shared creator link: show a clean read-only board, NEVER the admin editor
  channelName=channel||channelName; setCreatorHeader(channelName||(handle||"").replace(/^@/,""));
  const list=$("#list");list.innerHTML="";
  if(note){const n=document.createElement("div");n.className="pagenote";n.textContent=note;list.appendChild(n);}
  (ideas||[]).filter(Boolean).forEach((x,i)=>list.appendChild(leadCard(x,i+1)));
  return;
 }
 _pushScreen({v:"edit",h:handle||channelHandle||""});
 currentPageId=""; // in the admin editor, not on a saved page → no stray artifact writes should target the last-viewed page
 curateStyle=style||"leads";
 const _newHandle=handle||channelHandle;
 if(_newHandle!==curateHandle)curateRejected=[]; // switched to a different page → start a fresh reject pile
 if(rejected!==undefined)curateRejected=(rejected||[]).filter(Boolean); // opening a saved page → load its reject pile; regenerate passes undefined so the pile is preserved
 curateIdeas=(ideas||[]).map(x=>(x.title!=null)?{title:x.title||"",summary:x.summary||"",url:x.url||"",who:x.who||"",y:x.y||""}:{l:x.l||"",dirs:(x.dirs||[]).slice(0,2),url:x.url||"",who:x.who||"",y:x.y||""});
 curateHandle=handle||channelHandle; curateChannel=channel||channelName||curateHandle; curateProfile=(profile!==undefined&&profile!==null)?profile:channelProfile;
 inCurate=true; curateDirty=false;
 const b=$("#tbanner");if(b)b.style.display="none";
 const ah=$("#adminhome");if(ah)ah.style.display="none"; // focus: just the draft, not the start screen
 const pb=$("#poolbar");if(pb)pb.style.display="none";
 const list=$("#list");
 list.innerHTML='<div class="curstep">Reviewing the ideas for <b>'+esc(curateChannel)+'</b>. Every change saves automatically.</div>'+
  (researchNote?'<div class="rnote'+(researchNote[0]==="⚠"?' rwarn':'')+'">'+esc(researchNote)+'</div>':'')+
  '<div class="curbar"><div class="curtitle">'+esc(curTitle())+'</div>'+
  '<span class="saveind ok" id="saveind">All changes saved</span>'+
  '<button class="pubbtn" id="dopublish">Copy the link to send ↗</button>'+
  '<button class="navbtn" id="curcancel">← Back to start</button></div>'+
  '<div class="editnote">Click any line to edit it. Remove the weak ones, reorder with ▲▼, add a note shown at the top of their page. Everything saves automatically as you go, so there is no Save button. Changed your mind? Use ↩ Restore to bring back an earlier version.</div>'+
  '<div class="regenrow">Not a good fit? Start over with a fresh set for <b>'+esc(curateChannel)+'</b>: '+
   '<button class="regenbtn" id="regencustom">✍️ Write fresh ideas</button>'+
   '<button class="regenbtn" id="regenleads">📚 Use our library</button>'+
   '<button class="regenbtn" id="restorever" style="margin-left:auto">↩ Restore a previous version</button></div>'+
  '<textarea id="curnote" placeholder="Optional note to the creator (shown at the top of their page)">'+esc(note||"")+'</textarea>'+
  '<div class="addrow"><textarea id="addidea" rows="2" placeholder="Add your own idea, or paste a news story or a line from their longlist. It becomes an idea here, and gets a research pack + sample script like the rest."></textarea><button class="addbtn" id="addbtn">＋ Add idea</button></div>'+
  '<div id="ccards"></div>'+
  '<div id="rejwrap"></div>';
 researchNote=""; // one-shot: shown only for the fresh generation that set it, not on later edits/restores
 renderCards();
 renderRejected();
 $("#dopublish").onclick=publishCurrent;
 $("#curcancel").onclick=async()=>{await flushSave();showHome();}; // flush the pending save BEFORE leaving so nothing is dropped
 const nb=$("#curnote");if(nb)nb.oninput=()=>{curateDirty=true;scheduleSave();};
 $("#addbtn").onclick=addOwnIdea;
 const ai=$("#addidea");if(ai)ai.addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter"){e.preventDefault();addOwnIdea();}});
 $("#regencustom").onclick=()=>regenerate("custom");
 $("#regenleads").onclick=()=>regenerate("leads");
 $("#restorever").onclick=showRestore;
 scheduleSave(400); // persist the working set immediately so it can never be lost, even before any edit
}
// Replace the whole draft with a freshly generated set for the SAME channel, using either method.
// Solves: an existing page's ideas are bad and you want to redo them (esp. the old/from-scratch way).
async function regenerate(mode){
 const ch=curateChannel||curateHandle||"this channel";
 if(curateDirty && curateIdeas.length && !confirm("Replace this list with a freshly generated set for "+ch+"?\\n\\nThe edits you made here will be lost (your published page stays as-is until you publish again)."))return;
 const url=cleanChanUrl(curateHandle);
 if(!url){toast("No channel on this draft to regenerate");return;}
 const keepIdeas=curateIdeas.slice(), keepHandle=curateHandle, keepChannel=curateChannel, keepProfile=curateProfile, keepStyle=curateStyle;
 inCurate=false; // intentional replace — don't fire the leave-draft guard mid-generation
 $("#list").innerHTML='<div class="ptwrap" style="max-width:480px;margin:26px auto"></div>';
 const _stopR=progressTicker($("#list .ptwrap"), mode==="custom"?120:90, (mode==="custom"?"Writing fresh ideas for ":"Ranking our library for ")+ch);
 let ok=false;
 const _rej=curateRejected.map(ideaTitle).filter(Boolean); // steer the regenerate away from what we already rejected for this channel
 try{ ok = (mode==="custom") ? await fetchCustom(url,_rej) : await fetchTailor(url,false); }catch(e){ ok=false; }
 _stopR();
 // fetchCustom/fetchTailor rebuild the editor via startCurate on success. On failure, restore the old draft.
 if(!ok){ startCurate(keepIdeas,keepHandle,keepChannel,($("#curnote")&&$("#curnote").value)||"",keepProfile,keepStyle); toast("Could not generate a fresh set — kept your current list."); }
}
function renderCards(){
 const w=$("#ccards");if(!w)return;
 const ct=$(".curtitle");if(ct)ct.textContent=curTitle();
 w.innerHTML=curateIdeas.map((x,i)=>{
  const isIdea=x.title!=null;
  const head='<div><span class="cnum2">'+(i+1)+'</span><span class="ctxt" contenteditable="true">'+esc(isIdea?x.title:x.l)+'</span></div>';
  const body=isIdea?('<div class="cturn" contenteditable="true" data-sum="1">'+esc(x.summary||"")+'</div>'):((x.dirs||[]).map((d,j)=>'<div class="cturn" contenteditable="true" data-j="'+j+'">'+esc(d)+'</div>').join(""));
  return '<div class="ccard" data-i="'+i+'">'+head+body+'<div class="cctl"><button data-act="up">▲</button><button data-act="down">▼</button><button data-act="rm">✕ remove</button>'+
   (x.who?'<span class="dmeta" style="margin-left:auto">'+esc(x.who)+(x.y?" · "+esc(x.y):"")+'</span>':'')+'</div></div>';
 }).join("");
 w.querySelectorAll(".ccard").forEach(card=>{
  const i=+card.getAttribute("data-i");
  const tx=card.querySelector(".ctxt");tx.oninput=()=>{curateDirty=true;if(curateIdeas[i].title!=null)curateIdeas[i].title=tx.textContent.trim();else curateIdeas[i].l=tx.textContent.trim();scheduleSave();};
  card.querySelectorAll(".cturn").forEach(t=>{if(t.hasAttribute("data-sum")){t.oninput=()=>{curateDirty=true;curateIdeas[i].summary=t.textContent.trim();scheduleSave();};}else{const j=+t.getAttribute("data-j");t.oninput=()=>{curateDirty=true;curateIdeas[i].dirs[j]=t.textContent.trim();scheduleSave();};}});
  card.querySelector('[data-act="rm"]').onclick=()=>{curateDirty=true;const rm=curateIdeas.splice(i,1)[0];if(rm&&(ideaTitle(rm).trim()||ideaSummary(rm).trim()))curateRejected.unshift(rm);renderCards();renderRejected();scheduleSave();}; // removed ideas go to the reject pile (kept as signal), not discarded
  card.querySelector('[data-act="up"]').onclick=()=>{if(i>0){curateDirty=true;const a=curateIdeas;const tmp=a[i-1];a[i-1]=a[i];a[i]=tmp;renderCards();scheduleSave();}};
  card.querySelector('[data-act="down"]').onclick=()=>{if(i<curateIdeas.length-1){curateDirty=true;const a=curateIdeas;const tmp=a[i+1];a[i+1]=a[i];a[i]=tmp;renderCards();scheduleSave();}};
 });
}
// ---- add your own idea (paste a line from their longlist, or a news story) → becomes an idea card,
// then gets a research pack + sample script like the generated ones ----
function addOwnIdea(){
 const ta=$("#addidea");if(!ta)return;
 const v=(ta.value||"").trim();
 if(!v){toast("Type or paste an idea first");return;}
 // split into a title + summary so a pasted paragraph becomes a proper idea (first sentence/line = hook)
 let title=v, summary="";
 const nl=v.indexOf("\\n");
 if(nl>0){title=v.slice(0,nl).trim();summary=v.slice(nl+1).trim();}
 else{const m=v.match(/^(.{20,150}?[.!?])\s+(\S.*)$/); if(m){title=m[1].trim();summary=m[2].trim();}}
 if(title.length>220){summary=(title.slice(220).trim()+(summary?(" "+summary):"")).trim();title=title.slice(0,220).trim();}
 curateIdeas.push({title:title,summary:summary,url:"",who:"",y:""});
 curateDirty=true; ta.value="";
 renderCards();
 const cards=document.querySelectorAll("#ccards .ccard");if(cards.length)cards[cards.length-1].scrollIntoView({behavior:"smooth",block:"center"});
 toast("Added your idea — publish, then its pack + script are on the page");
 scheduleSave();
}
// ---- reject pile: removed ideas are kept here (not discarded) so we retain the signal of what we did
// NOT like. Restorable, persisted on the page, and copyable as liked+rejected for feeding to an AI. ----
function renderRejected(){
 const w=$("#rejwrap");if(!w)return;
 const n=curateRejected.length;
 if(!n){w.innerHTML="";return;}
 w.innerHTML='<details class="rejbox"><summary>🗑 Rejected ('+n+') <span class="rejhint">kept so we remember what to avoid</span> <button class="rejcopy" id="rejcopy">⧉ Copy liked + rejected</button></summary><div id="rejlist"></div></details>';
 const list=$("#rejlist");
 list.innerHTML=curateRejected.map((x,i)=>'<div class="rejrow"><span class="rejtxt">'+esc(ideaTitle(x))+'</span><button class="rejrestore" data-r="'+i+'">↩ restore</button></div>').join("");
 list.querySelectorAll("[data-r]").forEach(b=>b.onclick=()=>{const idx=+b.getAttribute("data-r");const it=curateRejected.splice(idx,1)[0];if(it)curateIdeas.push(it);curateDirty=true;renderCards();renderRejected();scheduleSave();});
 const cp=$("#rejcopy");if(cp)cp.onclick=(e)=>{e.preventDefault();e.stopPropagation();copyLikedRejected();};
}
function copyLikedRejected(){
 const liked=curateIdeas.filter(x=>ideaTitle(x).trim()).map(x=>"- "+ideaTitle(x));
 const rej=curateRejected.filter(x=>ideaTitle(x).trim()).map(x=>"- "+ideaTitle(x));
 const txt="Channel: "+curateChannel+"\\n\\nVIDEO IDEAS WE LIKED ("+liked.length+"):\\n"+liked.join("\\n")+"\\n\\nVIDEO IDEAS WE REJECTED ("+rej.length+"):\\n"+rej.join("\\n")+"\\n\\nPlease ideate more in the spirit of the liked ones, and avoid anything like the rejected ones.";
 navigator.clipboard.writeText(txt).then(()=>toast("Copied "+liked.length+" liked + "+rej.length+" rejected")).catch(()=>toast("Copy failed"));
}
async function publishCurrent(){
 if(!curateHandle){toast("No channel on this page");return;} // never write to the shared fallback doc id
 const note=(($("#curnote")&&$("#curnote").value)||"").trim();
 const ideas=curateIdeas.filter(x=>ideaTitle(x).trim().length>0||ideaSummary(x).trim().length>0); // keep anything with a title OR body; never drop a short title or a mid-edit blank-title card
 if(!ideas.length){toast("Nothing to publish");return;}
 const btn=$("#dopublish"),orig=btn?btn.textContent:"";if(btn){btn.disabled=true;btn.textContent="Publishing…";}
 try{
  // Firestore write promises stay PENDING while offline; bound it so the button can never
  // stick on "Publishing…". If it times out the write may still land, so say "couldn't confirm".
  const id=await withTimeout(savePage(curateHandle,curateChannel,note,ideas,curateProfile,curateStyle,curateRejected),15000);
  inCurate=false; // published — draft is safely saved, no more unsaved-work guard
  showPublished(location.origin+location.pathname+"?p="+encodeURIComponent(id),curateChannel,ideas.length);
 }catch(e){toast(String(e&&e.message)==="timeout"?"Couldn't confirm the save — check your connection and try again":"Publish failed — check connection");console.error(e);if(btn){btn.disabled=false;btn.textContent=orig;}}
}
function showPublished(link,channel,n){
 $("#list").innerHTML='<div class="sharepanel" style="flex-basis:auto"><div class="sharehead">✅ Published '+esc(channel)+"'s page ("+n+' videos)</div>'+
  '<div class="sharerow"><input class="sharelink" id="publink" readonly value="'+esc(link)+'">'+
  '<button class="sharebtn" id="pcopy">⧉ Copy link</button><button class="sharebtn ghost" id="ppreview">Preview ↗</button></div>'+
  '<div class="chansub" style="margin-top:8px">Send this to '+esc(channel)+'. Re-open it any time from 📄 My pages to edit and re-publish; the link stays the same.</div></div>'+
  '<div style="margin-top:16px;display:flex;gap:8px"><button class="navbtn" id="backpool">← Make another page</button><button class="navbtn" id="opendash">📄 My published pages</button></div>';
 const pl=$("#publink");if(pl&&pl.select)pl.select();
 $("#pcopy").onclick=()=>{const el=$("#publink");if(el)el.select();navigator.clipboard.writeText(link).then(()=>toast("Link copied")).catch(()=>toast("Copy failed"));};
 $("#ppreview").onclick=()=>window.open(link,"_blank","noopener");
 $("#backpool").onclick=()=>{showHome();};
 $("#opendash").onclick=showDashboard;
}
async function showDashboard(){
 if(!confirmLeaveDraft())return;
 _pushScreen({v:"dash"});
 currentPageId=""; // left the saved page for the dashboard → don't let stray saves target it
 ["editpagebtn","premakebtn","premakebar"].forEach(c=>{const e=document.querySelector("."+c);if(e)e.remove();});
 const b=$("#tbanner");if(b)b.style.display="none";
 const ah=$("#adminhome");if(ah)ah.style.display="none";
 const pb=$("#poolbar");if(pb)pb.style.display="none";
 const list=$("#list");
 list.innerHTML='<div class="curbar"><div class="curtitle">📄 Your published pages</div><button class="navbtn" id="dashback">← Back to start</button></div><div id="dashbody" class="editnote">Loading…</div>';
 $("#dashback").onclick=()=>{showHome();};
 if(!DB){const x=$("#dashbody");if(x)x.textContent="Cloud not available right now.";return;}
 try{
  const q=await withTimeout(DB.collection(PAGES_COL).orderBy("updated","desc").limit(500).get(),12000);
  const rows=q.docs.map(d=>{const o=d.data()||{};o.id=d.id;return o;});
  if(!rows.length){const x=$("#dashbody");if(x)x.textContent="No pages yet. Tailor a channel, curate, and hit Publish.";return;}
  $("#dashbody").outerHTML=rows.map(r=>{
   const link=location.origin+location.pathname+"?p="+encodeURIComponent(r.id);
   const when=r.updated?new Date(r.updated).toISOString().slice(0,10):"";
   return '<div class="dashrow"><span class="dchan">'+esc(r.channel||r.handle||r.id)+'</span><span class="dmeta">'+((r.ideas&&r.ideas.length)||0)+' videos · '+when+'</span>'+
    '<a href="'+esc(link)+'" target="_blank" rel="noopener">Open ↗</a><button data-edit="'+esc(r.id)+'">Edit</button><button data-copy="'+esc(link)+'">⧉ Copy</button></div>';
  }).join("");
  list.querySelectorAll("[data-copy]").forEach(bn=>bn.onclick=()=>{navigator.clipboard.writeText(bn.getAttribute("data-copy")).then(()=>toast("Link copied")).catch(()=>toast("Copy failed"));});
  list.querySelectorAll("[data-edit]").forEach(bn=>bn.onclick=async()=>{try{const doc=await loadPageDoc(bn.getAttribute("data-edit"));if(doc)startCurate(doc.ideas||[],doc.handle||bn.getAttribute("data-edit"),doc.channel||doc.handle,doc.note||"",doc.profile||"",doc.style||"leads",doc.rejected||[]);else toast("That page no longer exists — refresh the list.");}catch(e){toast("Could not open page — check your connection.");}});
 }catch(e){const x=$("#dashbody");if(x)x.textContent="Could not load pages ("+((e&&e.message)||e)+").";console.error(e);}
}
function leadCard(x,rank){
 const d=document.createElement("div");d.className="lead";
 const isIdea=x.title!=null; const main=isIdea?x.title:x.l;
 // Below the bold hook: a readable "what the video would actually do" paragraph (the summary). Rendered in
 // a legible secondary color, NOT the old dim gray that people skipped. The generator now writes this as a
 // substantive 2-3 sentence description, so it adds real context rather than restating the hook.
 const sub=isIdea?(x.summary||""):((x.dirs||[]).join(" "));
 d.innerHTML='<div class="rank">'+rank+'</div><div class="body"><div class="txt">'+esc(main)+'</div>'+(sub.trim()?'<div class="subtxt">'+esc(sub)+'</div>':'')+
  '<div class="meta">'+(x.who?'<span>'+esc(x.who)+(x.y?" · "+esc(x.y):"")+'</span>':'')+(x.url?'<a href="'+esc(x.url)+'" target="_blank" rel="noopener">read more ›</a>':'')+
  '<button class="packbtn">📄 Research pack</button><button class="scriptbtn">🎬 Sample script</button></div><div class="brief"></div><div class="scriptbox"></div></div>';
 d.querySelector(".packbtn").onclick=(e)=>loadBrief(d,x,e.currentTarget);
 d.querySelector(".scriptbtn").onclick=(e)=>loadScript(d,x,e.currentTarget);
 return d;
}
function withTimeout(p,ms){return Promise.race([p,new Promise((_,rej)=>setTimeout(()=>rej(new Error("timeout")),ms))]);}
function cloudErr(msg){return '<div class="simmsg simerr" style="padding:26px 0">'+esc(msg)+' <button class="navbtn" style="margin-left:8px" onclick="location.reload()">↻ Reload</button></div>';}
async function renderCreatorPage(id){
 _pushScreen({v:"page",id:String(id)});
 document.body.classList.add("creator");creatorView=true;
 $("#list").innerHTML='<div class="simmsg" style="padding:26px 0">Loading…</div>';
 setCreatorHeader(pageId(id));
 // three distinct states so a creator never sees a false "dead link" on a blip/adblock:
 if(!DB){$("#list").innerHTML=cloudErr("Couldn't reach the cloud to load this page. Check your connection (or turn off an ad blocker) and reload.");return;}
 let doc=null,failed=false;
 try{doc=await withTimeout(loadPageDoc(id),12000);}catch(e){failed=true;}
 if(failed){$("#list").innerHTML=cloudErr("Couldn't load this page right now. Check your connection and reload.");return;}
 if(!doc||!Array.isArray(doc.ideas)||!doc.ideas.filter(Boolean).length){$("#list").innerHTML='<div class="simmsg simerr" style="padding:26px 0">This page is not available. Ask for a fresh link.</div>';return;}
 doc.ideas=doc.ideas.filter(Boolean); // drop any null entries from older/externally-edited docs
 currentPageId=pageId(id); // packs/scripts opened here save ONTO this page so the creator sees them instantly
 channelName=doc.channel||doc.handle||id;channelHandle=doc.handle||("@"+pageId(id));channelProfile=doc.profile||"";creatorNote=doc.note||"";
 setCreatorHeader(channelName);
 const list=$("#list");list.innerHTML="";
 if(doc.note){const n=document.createElement("div");n.className="pagenote";n.textContent=doc.note;list.appendChild(n);}
 doc.ideas.forEach((x,i)=>list.appendChild(leadCard(x,i+1)));
 const more=document.createElement("button");more.className="morebtn";more.textContent="＋ Get more ideas for my channel";
 const shown=doc.ideas.slice();
 more.onclick=()=>generateMore(more,shown,doc.style||"leads");
 list.appendChild(more);
 if(isAdmin()){
  const old=document.querySelector(".editpagebtn");if(old)old.remove();
  const ed=document.createElement("button");ed.className="editpagebtn";ed.textContent="✏️ Edit this page";
  ed.title="Only you see this. Edit, reorder, or remove ideas, then re-publish to the same link.";
  ed.onclick=()=>{ed.remove();document.body.classList.remove("creator");creatorView=false;const pm2=document.querySelector(".premakebtn");if(pm2)pm2.remove();startCurate(doc.ideas,doc.handle||pageId(id),doc.channel||doc.handle||id,doc.note||"",doc.profile||"",doc.style||"leads",doc.rejected||[]);};
  document.body.appendChild(ed); // fixed top-right, always visible (admin only) — not buried at page bottom
  const oldpm=document.querySelector(".premakebtn");if(oldpm)oldpm.remove();
  const pm=document.createElement("button");pm.className="premakebtn";pm.textContent="⚡ Pre-make packs + scripts";
  pm.title="Only you see this. Generates the research pack + sample script for every idea and saves them onto this page, so the creator opens the link and sees them instantly. Takes a while — keep this tab open.";
  pm.onclick=()=>premakeAll(pm,doc.ideas);
  document.body.appendChild(pm);
 }
}
async function generateMore(btn,shown,style){
 if(btn.disabled)return; // guard double-fire
 btn.disabled=true;btn.textContent="Finding more…";
 const statusEl=document.createElement("div");statusEl.className="ptwrap";statusEl.style.cssText="margin:6px auto 14px;max-width:440px";btn.parentNode.insertBefore(statusEl,btn.nextSibling);
 const _stopMore=progressTicker(statusEl, style==="ideas"?70:35, "Finding more for "+channelName);
 const ctrl=new AbortController();const to=setTimeout(()=>ctrl.abort(),150000);
 try{
  const seen=new Set(shown.map(x=>key(ideaTitle(x))));
  let fresh=[];
  const opt={method:"POST",headers:{"Content-Type":"application/json"},signal:ctrl.signal};
  let r;
  if(style==="ideas"){
   r=await fetch(CUSTOM_API,{...opt,body:JSON.stringify({channelUrl:cleanChanUrl(channelHandle),exclude:shown.map(ideaTitle).slice(0,60),profile:channelProfile,channel:channelName})});
   if(r.status===429){btn.textContent="Busy — try again in a minute";return;}
   if(!r.ok)throw new Error("server");
   const j=await r.json();if(j&&j.profile)channelProfile=j.profile;
   fresh=((j&&j.ideas)||[]).filter(x=>ideaTitle(x)&&!seen.has(key(ideaTitle(x)))).slice(0,12);
  }else{
   r=await fetch(TAILOR_API,{...opt,body:JSON.stringify({channelUrl:cleanChanUrl(channelHandle),leads:topLeadsForTailor()})});
   if(r.status===429){btn.textContent="Busy — try again in a minute";return;}
   if(!r.ok)throw new Error("server");
   const j=await r.json();if(j&&j.profile)channelProfile=j.profile;
   fresh=(((j&&j.order)||[]).map(i=>LEADS[i])).filter(x=>x&&!seen.has(key(ideaTitle(x)))).slice(0,20);
  }
  fresh=fresh.slice(0,Math.max(0,55-shown.length)); // cap total ideas per page (keeps the doc + its history well under Firestore's 1MB limit)
  if(!fresh.length){btn.textContent=shown.length>=55?"That's plenty of ideas for one page":"No more to add right now";return;}
  if(!btn._divided){const dv=document.createElement("div");dv.className="moredivide";dv.textContent="More ideas for "+channelName;btn.parentNode.insertBefore(dv,btn);btn._divided=true;}
  fresh.forEach(x=>{shown.push(x);btn.parentNode.insertBefore(leadCard(x,shown.length),btn);});
  if(isAdmin()&&DB&&channelHandle){savePage(channelHandle,channelName,creatorNote,shown,channelProfile,style).then(()=>toast("Saved "+fresh.length+" new ideas to this page")).catch(()=>toast("Added them on screen, but the save failed — reload before relying on them"));} // admin: persist the additions; surface any failure (no silent catch)
  btn.textContent="＋ Get more ideas for my channel";
 }catch(e){btn.textContent=(e&&e.name==="AbortError")?"Took too long — try again":"Couldn't load more — try again";}
 finally{clearTimeout(to);_stopMore();statusEl.remove();btn.disabled=false;} // always re-enable + clear the ticker so nothing sticks
}
async function fetchCustom(rawurl,rejectedTitles){
 const url=cleanChanUrl(rawurl);if(!url){toast("Paste a channel link");return false;}
 const h=handleFromUrl(url);
 const btn=$("#cgen"),msg=$("#cmsg"),orig=btn?btn.textContent:"";
 if(btn){btn.disabled=true;btn.textContent="Generating…";}
 let _stopC=function(){};
 if(msg){msg.className="cmsg";msg.innerHTML='<div class="ptwrap"></div>';_stopC=progressTicker(msg.querySelector(".ptwrap"),120,"Writing fresh ideas");}
 try{
  const ctrl=new AbortController();const to=setTimeout(()=>ctrl.abort(),240000);
  const _cbody={channelUrl:url};if(Array.isArray(rejectedTitles)&&rejectedTitles.length)_cbody.rejected=rejectedTitles.slice(0,40); // feed the reject pile so a regenerate steers away from disliked ideas
  const r=await fetch(CUSTOM_API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(_cbody),signal:ctrl.signal});
  clearTimeout(to);
  if(r.status===429){if(msg){msg.className="cmsg err";msg.textContent="Busy right now — wait a minute and try again.";}return false;}
  const j=await r.json();
  if(j&&Array.isArray(j.ideas)&&j.ideas.length){
   channelName=j.channel||h; channelHandle=h; channelProfile=j.profile||"";
   const _nt=(j.research_meta&&j.research_meta.transcripts)||0; // make transcript usage VISIBLE (Drew: "is the transcript feature working?")
   researchNote=_nt>0 ? ("✓ Tailored using "+_nt+" of this channel's video transcripts (their actual words, not just titles).")
                      : "⚠ No transcripts were available for this channel, so these ideas are based on video titles and descriptions only.";
   if(msg)msg.textContent="";
   startCurate(j.ideas.slice(0,30), h, channelName, "", channelProfile, "ideas");
   return true;
  }
  if(msg){msg.className="cmsg err";msg.textContent=(j&&j.error)?j.error:"Could not generate ideas. Check the link.";}
  return false;
 }catch(e){if(msg){msg.className="cmsg err";msg.textContent="Took too long — try again.";}return false;}
 finally{_stopC();if(btn){btn.disabled=false;btn.textContent=orig||"✍️ Write fresh ideas for them";}}
}
function _genGuard(fn){if(genBusy||!confirmLeaveDraft())return;genBusy=true;Promise.resolve(fn()).finally(()=>{genBusy=false;});}
$("#cgo").onclick=()=>_genGuard(()=>fetchTailor($("#curl").value,true));
$("#cgen").onclick=()=>_genGuard(()=>fetchCustom($("#curl").value));
$("#curl").addEventListener("keydown",e=>{if(e.key==="Enter")_genGuard(()=>fetchCustom($("#curl").value));}); // Enter = the recommended (write-fresh) method
$("#cpages").onclick=showDashboard;
function enterPool(){_pushScreen({v:"pool"});inCurate=false;creatorView=false;document.body.classList.remove("creator");const ah=$("#adminhome");if(ah)ah.style.display="none";const pb=$("#poolbar");if(pb)pb.style.display="flex";const ch=$("#chead");if(ch)ch.innerHTML="";channelName="";channelHandle="";currentPageId="";tailored=false;fullView=false;tailorOrder=null;applyView();}
$("#browseall").onclick=()=>{if(!confirmLeaveDraft())return;enterPool();};
$("#poolback").onclick=()=>{showHome();};
$("#starred").onclick=()=>{onlyStar=!onlyStar;$("#starred").style.borderColor=onlyStar?"var(--gold)":"";render()};
$("#hidekilled").onclick=()=>{hideKilled=!hideKilled;$("#hidekilled").style.borderColor=hideKilled?"var(--red2)":"";render()};
$("#copykept").onclick=()=>{let n=0;const kept=viewOrder.filter(idx=>!killed.has(key(LEADS[idx].l))).map(idx=>{n++;return n+". "+LEADS[idx].l;}).join("\\n");navigator.clipboard.writeText(kept).then(()=>toast("Copied "+n+" kept leads")).catch(()=>toast("Copy failed"))};
$("#export").onclick=exportDecisions;
$("#import").onclick=()=>$("#importfile").click();
$("#importfile").onchange=e=>{const f=e.target.files&&e.target.files[0];if(f)importDecisions(f);e.target.value=""};
$("#reset").onclick=()=>{if(confirm("Clear all kills and stars?")){killed.clear();starred.clear();save();render()}};
// personal links: ?c=@handle → clean CREATOR view (admin chrome hidden), server-authoritative tailoring.
try{const _q=new URLSearchParams(location.search);const _pc=(_q.get("c")||"").trim();
 if(_q.get("admin")!=null)setAdmin();
 if(_pc){
  // creator ?c= shared link — a single ephemeral screen; no in-app history stack (Back leaves, as expected for a link)
  creatorView=true;
  document.body.classList.add("creator");
  const _purl=cleanChanUrl(_pc), _h=handleFromUrl(_purl);
  const ci=$("#curl");if(ci)ci.value=_purl;
  setCreatorHeader(_h.replace(/^@/,""));
  $("#list").innerHTML='<div class="ptwrap" style="max-width:480px;margin:26px auto"></div>';
  progressTicker($("#list .ptwrap"),90,"Loading the video ideas picked for "+_h.replace(/^@/,""));
  try{const cc=tload();if(cc[_h]){delete cc[_h];tsave(cc);}}catch(e){} // always refetch fresh for a shared link
  setTimeout(()=>{fetchTailor(_purl,false).then(ok=>{if(!ok)$("#list").innerHTML='<div class="simmsg simerr" style="padding:26px 0">Could not load these right now. Refresh in a minute?</div>';});},250);
 }else{
  // everything else routes through the screen model, so a reload on ?v=pages/pool/edit or a ?p= link restores that screen
  const _s=_screenFromLocation();
  if(_s.v!=="page")setAdmin(); // ?p= is a creator link; every other screen is the admin builder tool
  try{history.replaceState(_s,"",_urlFor(_s));}catch(e){} _navState=_s; // set the BASE entry (Back from here leaves the app)
  _popping=true; // initial paint must not push a second entry
  (async()=>{try{await _renderScreen(_s);}catch(e){showHome();}finally{_popping=false;}})();
 }
}catch(e){showHome();}
</script></body></html>"""

out = PAGE.replace("__DATA__", DATA).replace("__PV__", PV)

# Guard: this PAGE is a non-raw triple-quoted string, so a JS backslash-escape written as
# "\n" (instead of "\\n") gets eaten by Python into a real newline and silently breaks the
# ENTIRE <script> at parse time (the 0713.2328 bug: blank page, no console error). Extract the
# script and syntax-check it with node before we ever write/deploy. Fail loud, never ship broken JS.
import re, subprocess, tempfile
_m = re.search(r"<script>(.*)</script>", out, re.S)
if _m:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as _tf:
        _tf.write(_m.group(1)); _js = _tf.name
    try:
        _r = subprocess.run(["node", "--check", _js], capture_output=True, text=True)
        if _r.returncode != 0:
            raise SystemExit("ABORT: leads.html <script> has a JS syntax error, refusing to write.\n"
                             "Likely a Python-eaten escape (write \\\\n not \\n in the PAGE string).\n" + _r.stderr)
    except FileNotFoundError:
        print("WARN: node not found; skipped JS syntax check (install node to re-enable the guard)")
    finally:
        os.unlink(_js)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write(out)
open(OUT2, "w", encoding="utf-8").write(out)
print(f"wrote index.html + leads.html | {len(clean)} leads | v{PV}")
