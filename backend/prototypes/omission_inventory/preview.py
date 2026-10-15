"""Renders the PROPOSED inventory addition on top of the PINNED baseline request. Offline: it is written to a file and never sent.

The baseline is the real request the TC20 runs used: the pinned system prompt (criteria_extraction_v2-3) and the live user message
(job context plus JD between markers). Both are rebuilt through scripts/requirements_v2_tc20_eval.messages_for, and their hashes are
checked against the pinned values before anything is written. The ONLY difference is the marked block at the end of the user message.
That block lists the JD's source units in JD order, with ids. It adds no output field: the output schema is the baseline's.
"""
from __future__ import annotations

import hashlib
import json

import inventory as I
import replay as R
import requirements_v2_tc20_eval as ev

OUT = R.HERE / "preview" / "inventory_guided_request.txt"
SYSTEM_SHA256 = "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"
USER_SHA256 = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
ADD_BEGIN = "=== PROTOTYPE ADDITION (not in the baseline prompt; offline preview, not sent) ==="
ADD_END = "=== END PROTOTYPE ADDITION ==="
ADD_NOTE = ("The source units of the job description above, in JD order. Each has a stable id. A quote must still be copied exactly from the "
            "JD text. The output format is unchanged; no field is added.")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def addition_block(inv: I.Inventory) -> str:
    lines = [ADD_BEGIN, ADD_NOTE]
    for u in inv.units:
        lines.append(f"[{u.unit_id}] ({u.kind}) {u.text.strip()}")
    lines.append(ADD_END)
    return "\n".join(lines)


def render(jd: str, inv: I.Inventory) -> str:
    system, user = (m["content"] for m in ev.messages_for("v2-3", jd))
    if _sha(system) != SYSTEM_SHA256 or _sha(user) != USER_SHA256:
        raise SystemExit("baseline request does not match the pinned hashes")
    return (f"##### SYSTEM (pinned baseline, sha256 {SYSTEM_SHA256[:12]}) #####\n{system}\n"
            f"##### USER (baseline sha256 {USER_SHA256[:12]}, then the PROTOTYPE ADDITION) #####\n{user}\n\n{addition_block(inv)}\n")


def main() -> int:
    jd, _ = R.load_jd()
    inv = I.build_inventory(jd, json.loads(R.ANNOTATION.read_text(encoding="utf-8")))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(jd, inv), encoding="utf-8")
    print(f"wrote {OUT} ({len(inv.requirements())} requirement units, {len(inv.units)} units in JD order)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
