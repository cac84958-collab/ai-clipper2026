# ai-clipper

Un clone local d'OpusClip / Blitzreels — 100 % gratuit, hors-ligne (sauf le téléchargement
YouTube et le 1er téléchargement du modèle whisper).

Donne une vidéo longue (fichier **ou** lien YouTube) → l'outil :

1. la transcrit (faster-whisper, en local),
2. repère les meilleurs moments (LLM local Ollama / LM Studio, sinon heuristique),
3. les découpe et les **recadre en 9:16 en suivant le locuteur** (détection des visages + locuteur actif, *AI Reframe*),
4. ajoute des sous-titres animés **style OpusClip** (Montserrat ExtraBold, MAJUSCULES, mot courant surligné en vert),
5. écrit un rapport `clips.json` avec titres, scores de viralité et hashtags.

## Prérequis

- `ffmpeg` / `ffprobe` sur le PATH (déjà installés)
- `uv` (déjà installé) — gère le venv Python 3.11 et les dépendances
- *(optionnel, recommandé)* **Ollama** lancé avec un modèle, pour de meilleurs choix de clips :
  ```powershell
  ollama serve            # démarre le serveur
  ollama pull llama3.1    # un bon modèle généraliste (~4.7 Go)
  ```
  Sans LLM, l'outil bascule sur une sélection heuristique (ça marche, mais moins fin).

## Utilisation

### Interface web (façon OpusClip)

```powershell
# Depuis le dossier ai-clipper
uv run python -m clipper.server
```

Puis ouvre **http://127.0.0.1:8000** : colle un lien YouTube *ou* dépose une vidéo,
choisis les options, clique sur **Générer**. Les logs défilent en direct, puis chaque
clip s'affiche avec sa lecture, son score de viralité et un bouton de téléchargement.

### Ligne de commande

```powershell
uv run python -m clipper "https://www.youtube.com/watch?v=XXXX" --clips 3 --lang fr

# Ou un fichier local
uv run python -m clipper "C:\chemin\ma-video.mp4" -n 5 --layout blur
```

Les clips sortent dans `output/<titre-video>/`.

## Options principales

| Option | Défaut | Rôle |
|---|---|---|
| `-n, --clips` | 3 | nombre de clips à produire |
| `--min` / `--max` | 15 / 60 | durée min/max d'un clip (secondes) |
| `--model` | small | taille whisper : tiny/base/small/medium/large-v3 |
| `--lang` | auto | force la langue (ex. `fr`) — plus rapide et plus fiable |
| `--layout` | track | `track` (AI reframe : suit le locuteur), `crop` (crop centré fixe) ou `blur` (fond flou) |
| `--no-captions` | — | désactive les sous-titres |
| `--llm` | auto | `auto`/`none`/`ollama`/`lmstudio`/`<url>` |

## Limites connues (v1)

- AI Reframe = détection de visages **frontaux** (Haar) + choix du locuteur par mouvement de bouche (heuristique, pas un vrai modèle audio-visuel). Repli automatique sur crop centré si aucun visage.
- Transcription sur CPU : compter quelques minutes pour une longue vidéo.
- La qualité du repérage dépend du modèle LLM local utilisé.
