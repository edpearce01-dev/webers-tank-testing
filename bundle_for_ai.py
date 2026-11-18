"""
Packs the entire Weber's Tank Testing codebase into 2 clean text files inside _ai_context/
so you can upload the whole project into Gemini using only 2 of your 10 upload slots!
Usage: python bundle_for_ai.py
"""

from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "_ai_context"
OUT.mkdir(exist_ok=True)

BACKEND_FILES = [
    ROOT / "main.py",
    ROOT / "database.py",
    ROOT / "schemas.py",
    ROOT / "pdf_generator.py",
    ROOT / "bbq_db.py",
    ROOT / "bbq_pdf.py",
    *sorted((ROOT / "services").glob("*.py")),
    *sorted((ROOT / "routes").glob("*.py")),
]

FRONTEND_FILES = [
    *sorted((ROOT / "static").glob("*.html")),
    *sorted((ROOT / "static" / "js").glob("*.js")),
    *sorted((ROOT / "static").glob("*.js")),
    *sorted((ROOT / "static" / "css").glob("*.css")),
    *sorted((ROOT / "static").glob("*.css")),
]


def write_bundle(out_file: Path, title: str, files: list[Path]) -> int:
    chunks = [f"# {title}\n# Total files: {len([f for f in files if f.exists() and f.name != '__init__.py'])}\n"]
    count = 0
    for f in files:
        if not f.exists() or f.name == "__init__.py":
            continue
        rel = f.relative_to(ROOT).as_posix()
        txt = f.read_text(encoding="utf-8", errors="replace")
        chunks.append(f"\n{'=' * 76}\n=== FILE: {rel} ({len(txt.splitlines())} lines) ===\n{'=' * 76}\n{txt}\n")
        count += 1
    out_file.write_text("".join(chunks), encoding="utf-8")
    return count


b_cnt = write_bundle(OUT / "1_BACKEND_BUNDLE.txt", "WEBER'S TANK TESTING — BACKEND PYTHON BUNDLE", BACKEND_FILES)
f_cnt = write_bundle(OUT / "2_FRONTEND_BUNDLE.txt", "WEBER'S TANK TESTING — FRONTEND HTML/JS/CSS BUNDLE", FRONTEND_FILES)

print("\n======================================================================")
print(f"  AI BUNDLES READY IN: {OUT}")
print(f"  1) _ai_context/1_BACKEND_BUNDLE.txt  ({b_cnt} Python files)")
print(f"  2) _ai_context/2_FRONTEND_BUNDLE.txt ({f_cnt} HTML/JS/CSS files)")
print("  -> Drop just those 2 files into chat anytime you want a full review!")
print("======================================================================\n")
