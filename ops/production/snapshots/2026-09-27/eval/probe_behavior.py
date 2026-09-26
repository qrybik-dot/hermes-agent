#!/usr/bin/env python3
import argparse,json,os,shutil,subprocess,tempfile
from pathlib import Path
import yaml
ACTIVE=Path("/home/hermes/.hermes/profiles/hermes-native-core-canary")
HERMES="/home/hermes/.local/bin/hermes"
def prep(skill_file,root):
    home=Path(tempfile.mkdtemp(prefix="probe-",dir=root))
    cfg=yaml.safe_load((ACTIVE/"config.yaml").read_text())
    cfg["toolsets"]=[]; cfg["platform_toolsets"]={}; cfg["plugins"]={"enabled":[],"disabled":[],"entries":{}}; cfg["mcp_servers"]={}
    cfg.setdefault("model",{})["provider"]="custom:hermes-cli-proxy"
    cfg["model"]["default"]="gpt-oss-120b-medium"
    cfg.setdefault("memory",{})["memory_enabled"]=False; cfg["memory"]["user_profile_enabled"]=False
    cfg.setdefault("curator",{})["enabled"]=False
    cfg.setdefault("auxiliary",{}).setdefault("background_review",{})["enabled"]=False
    cfg.setdefault("skills",{})["write_approval"]=False; cfg["skills"]["creation_nudge_interval"]=0
    cfg["_config_version"]=44
    (home/"config.yaml").write_text(yaml.safe_dump(cfg,sort_keys=False,allow_unicode=True))
    if (ACTIVE/".env").exists(): (home/".env").symlink_to(ACTIVE/".env")
    d=home/"skills/productivity/call-card-verification/SKILL.md"; d.parent.mkdir(parents=True); shutil.copy2(skill_file,d)
    for rel in ["productivity/meeting-canonical-ingest/SKILL.md","note-taking/knowledge-card-curation/SKILL.md"]:
        src=ACTIVE/"skills"/rel; dst=home/"skills"/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
    return home
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--cases",required=True); ap.add_argument("--skill-file",required=True); ap.add_argument("--out-dir",required=True); ap.add_argument("--workspace-root",required=True); a=ap.parse_args()
    m=json.loads(Path(a.cases).read_text()); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True); root=Path(a.workspace_root); root.mkdir(parents=True,exist_ok=True)
    home=prep(Path(a.skill_file),root); env=os.environ.copy(); env["HERMES_HOME"]=str(home)
    fmt=("Это eval routing. НИЧЕГО не выполняй и не вызывай tools. Используй только preloaded skills. Ответь ровно 5 строками:\n"
         "selected_skill: <name>\ngranola_owner: <name-or-NONE>\nsave_flow: <короткий flow>\n"
         "side_effects: <ALLOW|DENY_UNLESS_EXPLICIT>\nspeaker_policy: <KNOWN|UNKNOWN_DO_NOT_GUESS>\n\n")
    preload="call-card-verification,meeting-canonical-ingest,knowledge-card-curation"
    for c in m["cases"]:
        cp=subprocess.run([HERMES,"-z",fmt+c["prompt"],"--skills",preload],env=env,text=True,capture_output=True,timeout=120)
        txt=cp.stdout or ""
        infra_error = cp.returncode != 0 or "API call failed" in txt or "HTTP 429" in txt or "Resource has been exhausted" in txt
        if cp.returncode: txt+="\nSTDERR:\n"+(cp.stderr or "")
        (out/(c["id"]+".txt")).write_text(txt)
        (out/(c["id"]+".meta.json")).write_text(json.dumps({"returncode":cp.returncode,"infra_error":infra_error,"model":"gpt-oss-120b-medium"},indent=2))
        if infra_error: raise RuntimeError(f"probe infrastructure error in {c[id]}: {txt[:200]}")
        print(c["id"],cp.returncode,len(txt),flush=True)
    print("eval_home",home)
if __name__=="__main__": main()
