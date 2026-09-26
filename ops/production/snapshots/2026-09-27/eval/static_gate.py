#!/usr/bin/env python3
import argparse, re
from pathlib import Path

def load_cases(path):
    rows=[]
    lines=[x.strip() for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")]
    hdr=lines[0].split("|")
    for line in lines[1:]:
        vals=line.split("|")
        rows.append(dict(zip(hdr, vals)))
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--target")
    ap.add_argument("--targets-root")
    ap.add_argument("--out", required=True)
    a=ap.parse_args()
    rows=load_cases(a.cases)
    out=[]
    ok_all=True
    for row in rows:
        target=row.get("target") or a.target
        if not target:
            raise SystemExit("target missing")
        p=Path(a.targets_root)/target if a.targets_root and not Path(target).is_absolute() else Path(target)
        text=p.read_text(encoding="utf-8")
        req=row.get("require") or row.get("required") or ""
        forbid=row.get("forbid") or row.get("forbidden") or ""
        missing=bool(req) and not re.search(req,text,re.I|re.S)
        bad=bool(forbid) and bool(re.search(forbid,text,re.I|re.S))
        ok=not missing and not bad
        ok_all &= ok
        detail=[]
        if missing: detail.append("missing:"+req)
        if bad: detail.append("forbidden:"+forbid)
        out.append(("PASS" if ok else "FAIL")+"|"+row.get("class","")+"|"+row.get("id","")+("|"+";".join(detail) if detail else ""))
    out.append("VERDICT="+("PASS" if ok_all else "FAIL"))
    Path(a.out).write_text("\n".join(out)+"\n",encoding="utf-8")
    print("\n".join(out))
    raise SystemExit(0 if ok_all else 2)

if __name__=="__main__":
    main()
