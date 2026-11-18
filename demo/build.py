"""Create two original fictional papers for the offline demo. Run from any directory."""
from pathlib import Path
import fitz
ROOT = Path(__file__).parent
A = "An Inventory of Lantern Patterns Across Imaginary Places"
B = "A Catalogue of Clouds in a Fictional Archipelago"

def make(filename, title, text, reference=None):
    doc = fitz.open()
    doc.set_metadata({"title": title, "author": "Evidence Desk fictional test fixture"})
    p = doc.new_page()
    p.insert_text((48,75), title, fontsize=17)
    p.insert_text((48,110), "Fictional rehearsal material, not real scientific evidence.", fontsize=10)
    p.insert_text((48,175), text, fontsize=11)
    if reference:
        p = doc.new_page()
        p.insert_text((48,80),"References",fontsize=15)
        p.insert_text((48,120),"Fictional Example, 2025. " + reference + ".",fontsize=10)
    doc.save(ROOT / filename)
    doc.close()

if __name__ == "__main__":
    make("A-lantern.pdf", A, "The amber lantern was recorded on the northern shore.")
    make("B-cloud.pdf", B, "The survey records fog banks on the southern shore.", A)
