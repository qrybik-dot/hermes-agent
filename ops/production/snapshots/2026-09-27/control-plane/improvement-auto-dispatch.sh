#!/usr/bin/env bash
set -euo pipefail
H=/home/hermes/.hermes/profiles/hermes-native-core-canary
LAB=/home/hermes/hermes-improvement-lab
P="$H/pending/skills"
Q=/opt/hermes-vps-admin-mcp/workspace/improvement-pending-quarantine.txt
mkdir -p "$LAB/workspaces/auto" "$P"
for f in "$P"/*.json; do
  [ -e "$f" ] || exit 0
  id=$(jq -r '.id // empty' "$f"); [ -n "$id" ] || id=$(basename "$f" .json)
  if [ -f "$Q" ] && grep -qxF "$id" "$Q"; then
    continue
  fi
  mapfile -t skills < <(jq -r 'if .payload.action=="batch" then .payload.operations[]?.name else .payload.name end // empty' "$f" | sort -u)
  [ ${#skills[@]} -gt 0 ] || skills=(unknown-skill)
  for skill in "${skills[@]}"; do
    ws="$LAB/workspaces/auto/$id/$skill"; mkdir -p "$ws"
    [ -f "$ws/.dispatched-r3" ] && continue
    status=NO_PROTECTED_EVAL
    if [ "$skill" = call-card-verification ]; then
      jq -r --arg s "$skill" 'if .payload.action=="batch" then (.payload.operations[] | select(.name==$s) | .content) else .payload.content end // empty' "$f" > "$ws/staged-candidate.SKILL.md"
      if [ -s "$ws/staged-candidate.SKILL.md" ]; then
        set +e
        python3 "$LAB/runner/static_gate.py" --cases "$LAB/cases/call-card-routing.tsv" --target "$ws/staged-candidate.SKILL.md" --out "$ws/protected-eval.txt" > "$ws/protected-eval.stdout.txt" 2>&1
        rc=$?; set -e; [ $rc -eq 0 ] && status=PASS || status=FAIL
        if grep -Eq '\\n- \[ \]|\\"' "$ws/staged-candidate.SKILL.md"; then
          status=FAIL
          printf '%s\n' 'FAIL|TrustSafety|structural-hygiene|literal escaped markdown detected' >> "$ws/protected-eval.txt"
        fi
      else status=UNMATERIALIZABLE; fi
    fi
    printf 'pending_id=%s\nskill=%s\nprotected_eval=%s\n' "$id" "$skill" "$status" > "$ws/event.txt"
    impl_body="EXECUTION-ONLY staged-skill task. pending_id=$id; skill=$skill; protected_eval=$status. Work ONLY inside the assigned workspace. Allowed reads: staged-candidate.SKILL.md and protected-eval.txt. Allowed writes: candidate/SKILL.md and CHANGELOG.md. Do NOT call search_files, do NOT inspect policy/runner/cases, do NOT read outside the workspace, do NOT rerun eval, and do NOT investigate infrastructure. If FAIL or malformed, make the smallest correction. If PASS, do not rewrite the candidate. A dependent reviewer already exists. As soon as outputs are ready, call kanban_complete and STOP. Never use request_review."
    HERMES_HOME="$H" HOME=/home/hermes PATH=/home/hermes/.local/bin:/usr/local/bin:/usr/bin:/bin /home/hermes/.local/bin/hermes kanban --board hermes-improvement create "Prepare staged skill $skill $id" --body "$impl_body" --assignee hermes-improvement-worker --workspace "dir:$ws" --created-by improvement-auto-dispatch --max-retries 1 --max-runtime 3m --completion-contract local-only --idempotency-key "pending-impl-r3-$id-$skill" --json > "$ws/dispatch-impl-r3.json" 2>&1
    impl_id=$(jq -r '.id' "$ws/dispatch-impl-r3.json")
    review_body="REVIEW-ONLY. pending_id=$id; skill=$skill; implementer=$impl_id. First tool call MUST run the control-plane wrapper /opt/hermes-vps-admin-mcp/workspace/review-call-card.sh with the assigned workspace path as its only argument. If wrapper returns VERDICT=PASS, next tool call MUST be kanban_complete with APPROVE and STOP. Otherwise next tool call MUST be kanban_block with REJECT and STOP. If terminal returns a process handle, wait for that process and do not launch the wrapper again. No find/read/search, no second shell command, no production/pending mutation."
    HERMES_HOME="$H" HOME=/home/hermes PATH=/home/hermes/.local/bin:/usr/local/bin:/usr/bin:/bin /home/hermes/.local/bin/hermes kanban --board hermes-improvement create "Independent review $skill $id" --body "$review_body" --assignee hermes-improvement-reviewer --parent "$impl_id" --workspace "dir:$ws" --created-by improvement-auto-dispatch --max-retries 1 --max-runtime 2m --model gpt-oss-120b-medium --provider custom:hermes-cli-proxy --completion-contract local-only --idempotency-key "pending-review-r4-$id-$skill" --json > "$ws/dispatch-review-r3.json" 2>&1
    : > "$ws/.dispatched-r3"
  done
done
