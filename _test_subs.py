"""Test rapide de la correction des sous-titres."""
from clipper.render import _merge_elisions, _group_words

# Phrase problématique : "la meilleure méta, c 'est de croiser un abonné"
words_test = [
    {"word": "la",      "start": 0.1, "end": 0.3},
    {"word": "meilleure", "start": 0.3, "end": 0.7},
    {"word": "meta,",   "start": 0.7, "end": 0.9},
    {"word": "c",       "start": 0.9, "end": 1.0},
    {"word": "'est",    "start": 1.0, "end": 1.1},
    {"word": "de",      "start": 1.1, "end": 1.2},
    {"word": "croiser", "start": 1.2, "end": 1.5},
    {"word": "un",      "start": 1.5, "end": 1.6},
    {"word": "abonne",  "start": 1.6, "end": 2.0},
]

merged = _merge_elisions(words_test)
print("=== APRES FUSION DES ELISIONS ===")
for w in merged:
    print(f"  [{w['start']:.2f}-{w['end']:.2f}] '{w['word']}'")

groups = _group_words(merged, group_size=4, max_gap=0.5)
print()
print("=== GROUPES (group_size=4) ===")
for i, g in enumerate(groups):
    words = [w["word"] for w in g]
    print(f"  Groupe {i+1}: {words}")
