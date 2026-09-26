#!/usr/bin/env python3
import argparse,json
from pathlib import Path
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--cases",required=True); ap.add_argument("--responses",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    m=json.loads(Path(a.cases).read_text()); rd=Path(a.responses); rows=[]; overall=True; layers={}
    for c in m["cases"]:
        p=rd/(c["id"]+".txt"); txt=p.read_text(errors="replace") if p.exists() else ""; lo=txt.lower()
        miss=[x for x in c["checks"].get("required",[]) if x.lower() not in lo]
        bad=[x for x in c["checks"].get("forbidden",[]) if x.lower() in lo]
        ok=bool(txt) and not miss and not bad; overall &= ok; layers.setdefault(c["layer"],[]).append(ok)
        rows.append({"id":c["id"],"layer":c["layer"],"pass":ok,"missing":miss,"forbidden":bad})
    out={"pass":overall,"layers":{k:all(v) for k,v in layers.items()},"cases":rows}
    Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n"); print(json.dumps(out,ensure_ascii=False,indent=2))
    raise SystemExit(0 if overall else 2)
if __name__=="__main__": main()
