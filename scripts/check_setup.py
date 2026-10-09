"""Post-install health check: data files, referential integrity, Excel consistency, Groq connectivity."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pharmasense import config  # noqa: E402
from pharmasense.data_layer import get_store  # noqa: E402
from pharmasense.llm import LLMClient, LLMError  # noqa: E402

ok = True
print("\n=== PharmaSense AI - setup check ===")
try:
    store = get_store()
    print("[OK] Loaded 7 tables:", ", ".join("%s(%d)" % (t, len(df)) for t, df in store.frames.items()))
    bad = [(n, v) for n, _, v in store.validate_integrity() if v]
    print("[OK] Referential integrity: all foreign keys valid" if not bad else "[WARN] FK violations: %s" % bad)
    mism = [(t, a, b) for t, a, b in store.consistency_with_excel() if a != b]
    print("[OK] CSV row counts match the Excel workbook" if not mism else "[WARN] CSV vs Excel mismatch: %s" % mism)
except Exception as e:  # noqa: BLE001
    print("[FAIL] Data layer:", e)
    ok = False

llm = LLMClient()
if not llm.available:
    print("[WARN] GROQ_API_KEY not set. Edit .env and paste your free key (https://console.groq.com/keys).")
else:
    try:
        r = llm.call_llm([{"role": "user", "content": "Reply with the single word: ready"}], config.MODEL_FALLBACK, max_tokens=200)
        print("[OK] Groq reachable (model %s answered: %r)" % (r.model, r.content.strip()[:40]))
    except LLMError as e:
        print("[WARN] Groq check failed:", e)
print("=== done ===\n")
sys.exit(0 if ok else 1)
