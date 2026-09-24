"""Per-product training data -> Needle fine-tune JSONL.

The pipeline is shared; each product supplies its own ``DomainDataset`` (Atlas:
owner-confirmed receipts/proposals/obligations/experiment decisions; Meemee:
personal-agent data; Sugarcode: its own). Rules enforced here, from Needle's
finetuning guide: arguments must be literally present in the query, optional
fields without evidence are omitted, and off-topic rows (``answers: []``) are
kept at roughly 1 in 8. Rows not marked confirmed are dropped; private rows are
kept but the manifest records that the dataset must train locally.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol


@dataclass
class ExampleRow:
    query: str
    tools: list[dict]
    answers: list[dict]
    confirmed: bool
    source_ref: str
    private: bool = True
    reasoning: str | None = None
    system: str | None = None
    meta: dict = field(default_factory=dict)
    product: str | None = None  # set by the product; rows tagged for another product are dropped


class DomainDataset(Protocol):
    product: str

    def rows(self) -> Iterable[ExampleRow]:
        """The product's own training rows (confirmed and tagged with the product)."""


def _values(obj) -> list[str]:
    if isinstance(obj, dict):
        return [v for x in obj.values() for v in _values(x)]
    if isinstance(obj, list):
        return [v for x in obj for v in _values(x)]
    if isinstance(obj, bool) or obj is None:
        return []
    return [str(obj)]


def check_row(row: ExampleRow) -> str | None:
    names = {t.get("name") for t in row.tools}
    for call in row.answers:
        if call.get("name") not in names:
            return f"answer calls unknown tool {call.get('name')!r}"
        for v in _values(call.get("arguments", {})):
            if v.strip() and v.casefold() not in row.query.casefold():
                return f"argument value {v!r} is not present in the query"
    if not row.query.strip():
        return "empty query"
    return None


def build_needle_jsonl(dataset: DomainDataset, out_path: str | Path, *, min_off_topic_ratio: float = 0.1) -> dict:
    kept, dropped, off_topic, private = [], [], 0, False
    for row in dataset.rows():
        if not row.confirmed:
            dropped.append({"source_ref": row.source_ref, "reason": "not owner-confirmed"})
            continue
        if row.product is not None and row.product != dataset.product:
            dropped.append({"source_ref": row.source_ref,
                            "reason": f"row belongs to {row.product!r}, not {dataset.product!r}"})
            continue
        why = check_row(row)
        if why:
            dropped.append({"source_ref": row.source_ref, "reason": why})
            continue
        rec = {"query": row.query, "tools": row.tools, "answers": row.answers}
        if row.reasoning:
            rec["reasoning"] = row.reasoning
        if row.system:
            rec["system"] = row.system
        kept.append(rec)
        off_topic += not row.answers
        private = private or row.private
    if not kept:
        raise ValueError("no usable rows after filtering")
    ratio = off_topic / len(kept)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in kept)
    out.write_text(text)
    manifest = {"product": dataset.product, "rows": len(kept), "dropped": len(dropped), "dropped_detail": dropped[:200],
                "off_topic_ratio": round(ratio, 3), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "train_locally_only": private, "path": str(out),
                "warnings": ([f"off-topic ratio {ratio:.2f} is below {min_off_topic_ratio}; the tuned model may call tools on everything"]
                             if ratio < min_off_topic_ratio else [])}
    Path(str(out) + ".manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
