"""Local control dashboard for the GLEE agent. Run it, open the URL, click Dispatch.

    export GLEE_API_KEY=glee_...
    python dashboard.py            # then open http://localhost:8787

Buttons shell out to the real scripts (play.py / run_search.py / run_eval.py) as one job at a
time (concurrent games on one account would collide), stream the log, and show live ratings.
The "Claude Code optimizer" button runs the `claude` CLI headless as the LLM optimizer if it's
on your PATH; otherwise it falls back to the built-in mutation search.
"""

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent
JOBLOG = REPO / "logs" / "dashboard_job.log"
JOBLOG.parent.mkdir(exist_ok=True)
PORT = int(os.environ.get("GLEE_DASH_PORT", "8787"))

_proc = None
_job = None


def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = str(REPO)
    return e


def busy():
    return _proc is not None and _proc.poll() is None


def start(name, cmd):
    global _proc, _job
    if busy():
        return False, f"busy with '{_job}'"
    with JOBLOG.open("w") as fh:
        fh.write(f"$ {' '.join(cmd)}\n\n")
    lf = JOBLOG.open("a")
    _proc = subprocess.Popen(cmd, cwd=str(REPO), env=_env(), stdout=lf,
                             stderr=subprocess.STDOUT, text=True)
    _job = name
    return True, f"started '{name}'"


def stats():
    try:
        from glee_sdk import GleeClient
        s = GleeClient(api_key=os.environ["GLEE_API_KEY"]).stats().get("scores", {})
        return {k: {"rating": round(v.get("rating", 0), 1), "games": v.get("games_played", 0)}
                for k, v in s.items()}
    except Exception as e:
        return {"error": str(e)}


def joblog_tail(n=160):
    if not JOBLOG.exists():
        return ""
    return "\n".join(JOBLOG.read_text(errors="replace").splitlines()[-n:])


PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>GLEE Agent — Control</title><style>
:root{--bg:#0e1220;--panel:#161b2b;--panel2:#1c2234;--ink:#e9eaf2;--soft:#9aa2b8;--line:#2a3247;
--gold:#e0ad55;--indigo:#8b93f5;--green:#58c08c;--coral:#e58a72;--fm:ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
h1{font-family:Georgia,serif;font-weight:600;font-size:26px;margin:0 0 4px}
.sub{color:var(--soft);font-size:14px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.tile .n{font-family:var(--fm);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.tile .v{font-family:Georgia,serif;font-size:30px;font-weight:600;margin-top:4px}
.tile .g{font-family:var(--fm);font-size:11px;color:var(--soft)}
.tile.barg .v{color:var(--gold)}.tile.neg .v{color:var(--indigo)}.tile.pers .v{color:var(--green)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{font-family:var(--fm);font-size:13px;cursor:pointer;border:1px solid var(--line);background:var(--panel2);color:var(--ink);border-radius:8px;padding:9px 15px}
.btn:hover{border-color:var(--gold)}
.btn.go{background:var(--gold);color:#20180a;border-color:var(--gold);font-weight:700}
.btn.stop{background:var(--coral);color:#241009;border-color:var(--coral);font-weight:700}
input{width:64px;font-family:var(--fm);background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px}
label{font-size:13px;color:var(--soft)}
.status{font-family:var(--fm);font-size:12px;padding:4px 10px;border-radius:6px;background:var(--panel2);color:var(--soft)}
.status.run{background:#173427;color:var(--green)}
pre{background:#0a0e18;border:1px solid var(--line);border-radius:10px;padding:14px;height:340px;overflow:auto;
font-family:var(--fm);font-size:12px;color:#c7cede;white-space:pre-wrap;margin:0}
.hint{font-size:12px;color:var(--soft);margin-top:8px}
.sect{font-family:var(--fm);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.cap{font-family:var(--fm);font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);margin-bottom:8px}
.optgrid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:720px){.optgrid{grid-template-columns:1fr}}
.archgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}
.acell{aspect-ratio:1.5;border:1px solid var(--line);border-radius:6px;display:flex;align-items:center;justify-content:center;font-family:var(--fm);font-size:11px;color:var(--soft);background:var(--panel2)}
.acell.filled{color:#20180a;font-weight:700}
.acell.best{outline:2px solid var(--green);outline-offset:-2px}
.axnote{font-family:var(--fm);font-size:10px;color:var(--soft);margin-top:6px}
.episodes{display:flex;flex-direction:column;gap:4px;font-family:var(--fm);font-size:12px}
.ep{display:flex;gap:12px;padding:6px 9px;border:1px solid var(--line);border-radius:6px;background:var(--panel2);align-items:center}
.ep .v{color:var(--soft)} .ep .adopt{color:var(--green);font-weight:700} .ep .reject{color:var(--coral)}
canvas#ratechart{max-width:100%;background:#0a0e18;border:1px solid var(--line);border-radius:8px}
</style></head><body><div class=wrap>
<h1>GLEE Agent — Control</h1>
<div class=sub>Dispatch games and the optimizer. One job runs at a time (concurrent games collide). Live ratings refresh every 5s.</div>

<div class=grid>
 <div class="tile barg"><div class=n>Bargaining</div><div class=v id=r-bargaining>—</div><div class=g id=g-bargaining></div></div>
 <div class="tile neg"><div class=n>Negotiation</div><div class=v id=r-negotiation>—</div><div class=g id=g-negotiation></div></div>
 <div class="tile pers"><div class=n>Persuasion</div><div class=v id=r-persuasion>—</div><div class=g id=g-persuasion></div></div>
</div>

<div class=panel>
 <div class=row style="margin-bottom:12px">
  <span style="font-size:13px;color:var(--soft)">families:</span>
  <label><input type=checkbox class=fam value=bargaining checked> bargaining</label>
  <label><input type=checkbox class=fam value=negotiation checked> negotiation</label>
  <label><input type=checkbox class=fam value=persuasion checked> persuasion</label>
 </div>
 <div class=row>
  <label>games <input id=ngames type=number value=12></label>
  <button class="btn go" onclick="disp('play','&n='+val('ngames')+'&families='+fams())">▶ Play</button>
  <label>episodes <input id=iters type=number value=6></label>
  <label>games/ep <input id=evaln type=number value=16></label>
  <button class="btn go" onclick="disp('train','&steps='+val('iters')+'&evaln='+val('evaln')+'&families='+fams())">⟳ Train</button>
  <button class="btn" onclick="if(confirm('Revert live agent to the last champion snapshot?'))disp('revert','')">↩ Revert</button>
  <button class="btn stop" onclick="stop()">■ Stop</button>
 </div>
 <div class=hint><b>Play</b> — inference: run the current agent to accumulate rating &amp; data (no learning). <b>Train</b> — each episode: the LLM optimizer (Claude Code) proposes a ruleset tweak from accumulated Reflexion lessons, then a held-out A/B plays champion vs candidate for <i>games/ep</i> each and <b>only deploys if the candidate is confidently better</b> (bootstrap P≥0.8, ≥8 games/arm) — else keeps the champion. Raise <i>games/ep</i> to cut noise. Champion snapshot saved automatically. <b>Revert</b> — restore that snapshot. <b>Stop</b> — halt the running job. Pick <b>families</b> to control the game mix — Train now tunes persuasion too (seller honesty + buyer skepticism).</div>
</div>

<div class=panel>
 <div class=row style="justify-content:space-between;margin-bottom:10px">
  <b style="font-family:var(--fm);font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--soft)">Job log</b>
  <span class=status id=status>idle</span>
 </div>
 <pre id=log>waiting…</pre>
</div>

<div class=panel>
 <b class=sect>Optimization</b>
 <div class=optgrid style="margin-top:12px">
  <div>
   <div class=cap>behavior archive — each cell = best ruleset found there, color = fitness</div>
   <div id=archgrid class=archgrid></div>
   <div class=axnote>columns → capture / share (low→high) &nbsp;·&nbsp; rows ↓ no-deal rate (low→high). Bottom-right ≈ ideal. This is quality-diversity filling behavior space.</div>
  </div>
  <div>
   <div class=cap>ratings over time (live)</div>
   <canvas id=ratechart width=440 height=210></canvas>
   <div class=axnote><span style="color:var(--gold)">━ bargaining</span> &nbsp; <span style="color:var(--indigo)">━ negotiation</span> &nbsp; <span style="color:var(--green)">━ persuasion</span></div>
  </div>
 </div>
 <div class=cap style="margin-top:16px">recent training episodes — held-out A/B verdicts</div>
 <div id=episodes class=episodes>run Train to populate…</div>
</div>

<script>
function val(id){return document.getElementById(id).value||'0'}
function fams(){return [...document.querySelectorAll('.fam:checked')].map(c=>c.value).join(',')||'negotiation,bargaining'}
async function disp(job,extra){
 const r=await fetch('/dispatch?job='+job+extra,{method:'POST'});const t=await r.text();
 document.getElementById('status').textContent=t;
}
async function stop(){await fetch('/stop',{method:'POST'});}
const RATE_HIST=[];
async function refreshStats(){
 try{const s=await (await fetch('/stats')).json();
  for(const f of ['bargaining','negotiation','persuasion']){
   const d=s[f];document.getElementById('r-'+f).textContent=d?d.rating:'—';
   document.getElementById('g-'+f).textContent=d?(d.games+' games'):'';}
  if(s.bargaining){RATE_HIST.push({b:s.bargaining&&s.bargaining.rating,n:s.negotiation&&s.negotiation.rating,p:s.persuasion&&s.persuasion.rating});
   if(RATE_HIST.length>150)RATE_HIST.shift();drawRates();}
 }catch(e){}
}
async function refreshLog(){
 try{const j=await (await fetch('/joblog')).json();
  document.getElementById('log').textContent=j.log||'(empty)';
  const st=document.getElementById('status');
  st.textContent=j.running?('running: '+j.job):'idle';
  st.className='status'+(j.running?' run':'');
  const pre=document.getElementById('log');pre.scrollTop=pre.scrollHeight;
 }catch(e){}
}
function drawRates(){
 const c=document.getElementById('ratechart');if(!c||RATE_HIST.length<2)return;
 const x=c.getContext('2d'),W=c.width,H=c.height,pad=30;x.clearRect(0,0,W,H);
 const ser=[['b','#e0ad55'],['n','#8b93f5'],['p','#58c08c']];
 let lo=1e9,hi=-1e9;RATE_HIST.forEach(r=>ser.forEach(s=>{const v=r[s[0]];if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v);}}));
 lo=Math.floor(lo/50)*50-10;hi=Math.ceil(hi/50)*50+10;
 const X=i=>pad+(W-pad-8)*i/(RATE_HIST.length-1), Y=v=>H-pad-(H-pad-8)*(v-lo)/((hi-lo)||1);
 x.strokeStyle='#2a3247';x.beginPath();x.moveTo(pad,8);x.lineTo(pad,H-pad);x.lineTo(W-8,H-pad);x.stroke();
 x.fillStyle='#6b7288';x.font='10px monospace';x.fillText(hi,2,12);x.fillText(lo,2,H-pad+4);
 ser.forEach(s=>{x.strokeStyle=s[1];x.lineWidth=2;x.beginPath();let st=false;
  RATE_HIST.forEach((r,i)=>{const v=r[s[0]];if(v==null)return;const px=X(i),py=Y(v);st?x.lineTo(px,py):x.moveTo(px,py);st=true;});x.stroke();});
}
async function refreshArchive(){
 try{const a=await (await fetch('/archive')).json();let best=-1;
  for(const k in a){const f=a[k].metrics&&a[k].metrics.fitness;if(f>best)best=f;}
  const g=document.getElementById('archgrid');g.innerHTML='';
  for(let nb=0;nb<4;nb++)for(let sb=0;sb<5;sb++){
   const key=sb+'-'+nb,cell=a[key],d=document.createElement('div');d.className='acell';
   if(cell){const f=(cell.metrics&&cell.metrics.fitness)||0;d.classList.add('filled');
    d.style.background='rgba(224,173,85,'+(0.2+0.8*Math.max(0,Math.min(1,f)))+')';
    d.textContent=f.toFixed(2);d.title='cell '+key+' · fitness '+f;if(f===best)d.classList.add('best');}
   g.appendChild(d);}
 }catch(e){}
}
async function refreshHistory(){
 try{const h=await (await fetch('/history')).json();const e=document.getElementById('episodes');
  if(!h.length){e.textContent='run Train to populate…';return;}
  e.innerHTML='';
  h.slice().reverse().forEach(r=>{const row=document.createElement('div');row.className='ep';row.style.flexDirection='column';row.style.alignItems='stretch';
   const head='<div style="display:flex;gap:12px;align-items:center"><span>ep '+r.ep+'</span>'
     +'<span class=v>'+(r.src||'')+'</span>'
     +'<span class=v>'+(r.champ_share!=null?('champ '+r.champ_share+' vs cand '+r.cand_share):'')+'</span>'
     +'<span class=v>P='+(r.P==null?'—':r.P)+'</span>'
     +'<span class="'+(r.adopt?'adopt':'reject')+'" style="margin-left:auto">'+(r.adopt?'ADOPT':'reject')+'</span></div>';
   const think=(r.thinking?'<div class=v style="margin-top:4px">🧠 '+r.thinking+'</div>':'')
     +'<div class=v style="margin-top:2px;opacity:.8">patch '+JSON.stringify(r.patch||{})+'</div>';
   row.innerHTML=head+think;e.appendChild(row);});
 }catch(e){}
}
setInterval(refreshStats,5000);setInterval(refreshLog,2000);setInterval(refreshArchive,6000);setInterval(refreshHistory,6000);
refreshStats();refreshLog();refreshArchive();refreshHistory();
</script></div></body></html>"""


def optimizer_cmd():
    """Prefer the Claude Code CLI as the LLM optimizer; else built-in mutation search."""
    if shutil.which("claude"):
        instr = (
            "You are optimizing a GLEE agent. Working dir is this repo. Read tw/params.py for the "
            "tunable knobs and their BOUNDS. Run `python play.py 8 negotiation,bargaining` to get "
            "fresh ratings, inspect logs/games.jsonl, then edit ONLY params.json within bounds "
            "(1-3 small knob changes). Re-run play.py to check. If ratings drop, revert your edit. "
            "Do 3 rounds, then stop and summarize. Never edit .py files or the API key."
        )
        return ["claude", "-p", instr, "--permission-mode", "acceptEdits"]
    return [sys.executable, "run_search.py"]


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            self._send(200, PAGE, "text/html")
        elif p == "/stats":
            self._send(200, json.dumps(stats()), "application/json")
        elif p == "/joblog":
            self._send(200, json.dumps({"log": joblog_tail(), "running": busy(), "job": _job}),
                       "application/json")
        elif p == "/archive":
            fp = REPO / "logs" / "archive.json"
            self._send(200, fp.read_text() if fp.exists() else "{}", "application/json")
        elif p == "/history":
            fp = REPO / "logs" / "hypotheses.jsonl"
            rows = []
            if fp.exists():
                for ln in fp.read_text().splitlines()[-12:]:
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
            self._send(200, json.dumps(rows), "application/json")
        else:
            self._send(404, "not found")

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/stop":
            if busy():
                _proc.terminate()
            self._send(200, "stopping"); return
        if u.path == "/dispatch" and q.get("job", [""])[0] == "revert":
            try:
                shutil.copy(REPO / "params_prechampion.json", REPO / "params.json")
                self._send(200, "reverted to champion snapshot")
            except Exception as e:
                self._send(200, f"no snapshot to revert to ({e})")
            return
        if u.path == "/dispatch":
            job = q.get("job", [""])[0]
            fam = q.get("families", ["negotiation,bargaining,persuasion"])[0]
            if job == "play":
                n = q.get("n", ["12"])[0]
                ok, msg = start(f"play {n} [{fam}]", [sys.executable, "-u", "play.py", n, fam])
            elif job == "train":
                steps = q.get("steps", ["6"])[0]
                evaln = q.get("evaln", ["16"])[0]
                ok, msg = start(f"train {steps}ep x{evaln}/arm [{fam}]",
                                [sys.executable, "-u", "-c",
                                 f"import os;os.environ['GLEE_TRAIN_STEPS']='{steps}';"
                                 f"os.environ['GLEE_EVAL_N']='{evaln}';os.environ['GLEE_FAMILIES']='{fam}';"
                                 "import train;train.main()"])
            else:
                ok, msg = False, "unknown job"
            self._send(200, msg); return
        self._send(404, "not found")


if __name__ == "__main__":
    if not os.environ.get("GLEE_API_KEY"):
        print("WARNING: set GLEE_API_KEY first (export GLEE_API_KEY=glee_...)")
    print(f"GLEE dashboard → http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
