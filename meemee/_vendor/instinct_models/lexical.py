"""LexicalToolModel: a small, real, trainable tool-call model with no dependencies.

This is NOT a language model and cannot write prose. It is a classical model:
multinomial naive Bayes over word and bigram features picks one tool (or abstains),
and a cue-word extractor fills arguments with spans copied word-for-word from the
query. It exists so the Router has a real free local route that runs on any machine
with no download, server or key. It is weaker than Needle and is meant as the floor,
not the ceiling: it escalates (returns no call) whenever it is not confident.

Heuristics, named as such: tool choice is lexical; argument extraction uses learned
"cue" words that precede a value in training queries, plus patterns for numbers,
emails, ISO dates and enum values.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .providers import LOCAL, ChatResult, Provider, ProviderUnavailable

NONE = "__none__"
_TOK = re.compile(r"[A-Za-z0-9@._'-]+")
_STOP = {"the", "a", "an", "to", "of", "and", "for", "on", "in", "by", "with", "please", "my", "is", "it"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_QUOTED = re.compile(r"\"([^\"]+)\"|'([^']+)'")


def _words(text: str) -> list[str]:
    return [w.strip(".,'-").lower() for w in _TOK.findall(text) if w.strip(".,'-")]


def _feats(text: str) -> list[str]:
    w = [x for x in _words(text) if x not in _STOP]
    return w + [f"{a}_{b}" for a, b in zip(w, w[1:])]


class LexicalToolModel:
    def __init__(self, min_confidence: float = 0.6, alpha: float = 0.3):
        self.min_confidence, self.alpha = min_confidence, alpha
        self.class_n: Counter = Counter()
        self.feat_n: dict[str, Counter] = defaultdict(Counter)
        self.vocab: set[str] = set()
        self.cues: dict[str, Counter] = defaultdict(Counter)  # "tool.param" -> cue word counts
        self.ends: dict[str, Counter] = defaultdict(Counter)  # "tool.param" -> word that follows the value
        self.trained_rows = 0

    # ---- training -------------------------------------------------------
    def fit(self, rows: list[dict]) -> "LexicalToolModel":
        """rows: Needle-format records {query, tools, answers}."""
        for r in rows:
            q = r["query"]
            label = r["answers"][0]["name"] if r.get("answers") else NONE
            self._add(label, _feats(q))
            for call in r.get("answers", []):
                for p, v in (call.get("arguments") or {}).items():
                    cue = self._cue_before(q, str(v))
                    if cue:
                        self.cues[f"{call['name']}.{p}"][cue] += 1
                    end = self._word_after(q, str(v))
                    if end:
                        self.ends[f"{call['name']}.{p}"][end] += 1
            for t in r.get("tools", []):  # weak prior from tool name + description
                self._add(t["name"], _feats(t["name"].replace("_", " ") + " " + t.get("description", "")), weight=0.5)
            self.trained_rows += 1
        return self

    @classmethod
    def from_jsonl(cls, path: str | Path, **kw) -> "LexicalToolModel":
        rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
        return cls(**kw).fit(rows)

    def _add(self, label, feats, weight=1.0):
        self.class_n[label] += weight
        for f in feats:
            self.feat_n[label][f] += weight
            self.vocab.add(f)

    @staticmethod
    def _word_after(query: str, value: str) -> str | None:
        i = query.casefold().find(value.casefold())
        if i < 0:
            return None
        nxt = _words(query[i + len(value):])
        return nxt[0] if nxt else None

    @staticmethod
    def _cue_before(query: str, value: str) -> str | None:
        i = query.casefold().find(value.casefold())
        if i <= 0:
            return None
        prev = _words(query[:i])
        return prev[-1] if prev else None

    # ---- inference ------------------------------------------------------
    def classify(self, query: str, allowed: list[str]) -> tuple[str, float]:
        labels = [l for l in allowed if self.class_n[l]] + ([NONE] if self.class_n[NONE] else [])
        if not labels:
            return NONE, 0.0
        feats = _feats(query)
        total = sum(self.class_n[l] for l in labels)
        v = max(len(self.vocab), 1)
        logp = {}
        for l in labels:
            denom = sum(self.feat_n[l].values()) + self.alpha * v
            logp[l] = math.log(self.class_n[l] / total) + sum(math.log((self.feat_n[l][f] + self.alpha) / denom) for f in feats)
        m = max(logp.values())
        z = sum(math.exp(x - m) for x in logp.values())
        best = max(logp, key=logp.get)
        return best, math.exp(logp[best] - m) / z

    def extract(self, query: str, tool: dict) -> dict:
        props = (tool.get("parameters") or {}).get("properties", {})
        out: dict = {}
        quoted = [a or b for a, b in _QUOTED.findall(query)]
        for p, spec in props.items():
            t = spec.get("type", "string")
            val = None
            if spec.get("enum"):
                val = next((e for e in spec["enum"] if str(e).casefold() in query.casefold()), None)
            elif t in ("integer", "number"):
                m = _NUM.search(query)
                val = (float(m.group()) if "." in m.group() else int(m.group())) if m else None
            elif "email" in p.lower() and _EMAIL.search(query):
                val = _EMAIL.search(query).group()
            elif ("date" in p.lower() or p.lower() in ("due", "deadline")) and _DATE.search(query):
                val = _DATE.search(query).group()
            else:
                cues = self.cues.get(f"{tool['name']}.{p}")
                val = self._span_after_cue(query, set(cues or []), set(self.ends.get(f"{tool['name']}.{p}") or [])) or (quoted.pop(0) if quoted else None)
            if val is not None and str(val).casefold() in query.casefold():
                out[p] = val
        return out

    @staticmethod
    def _span_after_cue(query: str, cues: set[str], ends: set[str] = frozenset()) -> str | None:
        if not cues:
            return None
        toks = list(re.finditer(r"[A-Za-z0-9@._'-]+", query))
        for i, m in enumerate(toks):
            if m.group().strip(".,'-").lower() in cues and i + 1 < len(toks):
                span = []
                for n in toks[i + 1:i + 5]:
                    w = n.group()
                    if (w.lower() in _STOP or w.lower() in ends) and span:
                        break
                    span.append(n)
                    if query[n.end():n.end() + 1] in ",;:" :
                        break
                if span:
                    return query[span[0].start():span[-1].end()].rstrip(".,;:")
        return None

    def predict(self, query: str, tools: list[dict]) -> dict | None:
        """Return {name, arguments, confidence} or None (abstain => escalate)."""
        by = {t["name"]: t for t in tools}
        name, conf = self.classify(query, list(by))
        if name == NONE or conf < self.min_confidence:
            return None
        args = self.extract(query, by[name])
        required = (by[name].get("parameters") or {}).get("required", [])
        if any(r not in args for r in required):
            return None
        return {"name": name, "arguments": args, "confidence": round(conf, 3)}


class LexicalLocal(Provider):
    """Router provider around LexicalToolModel. Tool-calling only; never writes prose."""
    name, locality = "lexical-local", LOCAL

    def __init__(self, model: LexicalToolModel | None = None):
        self.model = model

    def available(self) -> bool:
        return self.model is not None and self.model.trained_rows > 0

    def chat(self, messages, *, tools=None, max_tokens=1024):
        if not self.available():
            raise ProviderUnavailable("lexical model is not trained")
        if not tools:
            raise ProviderUnavailable("lexical model only makes tool calls")
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        call = self.model.predict(user, tools)
        calls = [{"name": call["name"], "arguments": call["arguments"]}] if call else []
        return ChatResult(self.name, "lexical-nb", "", calls, {"prediction": call})
