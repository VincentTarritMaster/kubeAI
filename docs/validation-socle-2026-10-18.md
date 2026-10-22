# Validation du socle kubeAI — étape 1

> Rapport historique du 18 octobre 2026. Il décrit le socle initial (27 tests,
> sans cAdvisor ni démonstrations complètes). L’état actuel comporte cAdvisor,
> 360 tests automatisés et les scénarios A/B/C ; voir le
> [README](../README.md) et [l’architecture](architecture.md).

Projet collectif de Kevin, Vincent et Anass. Contrôle effectué le 18 octobre 2026, sous Windows, avec Python 3.12.1 et pytest 8.4.2.

## 1. Branche et commit de départ

La branche locale `integration/kubeai` a été créée depuis `AnassNDRI`, au commit `23260c91b45ed202e6aef61576263d6cbd1cb615`. Aucun renommage n'a été nécessaire : aucune ancienne branche d'intégration n'existait. Aucun nouveau commit, push ou fusion n'a été effectué. Les autres branches conservent leurs références.

## 2. Synchronisation distante

`git fetch origin` a réussi après autorisation d'écriture dans Git et d'accès réseau. Le premier essai limité par le bac à sable avait échoué sur `.git/FETCH_HEAD`.

Après synchronisation, aucun commit de `origin/main`, `origin/AnassNDRI`, `origin/kevin` ou `origin/feat/llm-local` ne manque à `AnassNDRI`. La vérification par `git log AnassNDRI..référence` n'a produit aucune ligne.

## 3. Commandes exécutées

Les textes exacts des commandes, leurs contextes et les échecs intermédiaires figurent dans l'annexe ci-dessous. Les commandes de reproduction essentielles figurent en section 8. Les appels de consultation ou de création du présent rapport ne sont pas des commandes shell.

## 4. Résultats

| Vérification exécutée | Résultat observé |
|---|---|
| Compilation syntaxique en mémoire des sources Python, hors environnements/cache/documents locaux | 27 fichiers valides |
| pytest sur `tests/test_api.py` et `tests/test_chat.py` | **27 réussis, 0 échoué, 0 ignoré**, en 12,52 s |
| Tests live | 5 méthodes existantes, exclues de la collecte ; **non exécutées** |
| `pip check` dans l'environnement virtuel | Aucun conflit |
| Comparaison avec `agent/requirements.txt` | Les 17 versions runtime correspondent exactement |
| Démarrage FastAPI par défaut, hors cluster | Certificat CA Kubernetes absent : `FileNotFoundError` attendu et constaté |
| Démarrage FastAPI avec configuration locale, en mémoire | Réussi ; appels HTTP sortants interdits par le contrôle |
| `/health`, `/openapi.json`, `/v1/models` avec cette configuration | Réussis ; sept routes attendues présentes au schéma |
| Namespace invalide, trois bornes numériques invalides, variable numérique non numérique | Les cinq cas sont rejetés par `ValueError` |
| Exclusions Git | `.venv`, `.env` et caches ignorés ; `.env.example` conservé |
| Configuration d'exemple | Profil cluster sans secret réel ; fichier non chargé automatiquement |

Les tests API et chat utilisent des transports HTTP simulés. Le contrôle de démarrage n'ouvre aucun port et bloque `httpx.AsyncClient.send`. Aucun cluster, conteneur, monitoring ou LLM n'a été lancé. Aucun appel Mistral ni téléchargement de modèle n'a été effectué.

## 5. Fichiers créés ou modifiés

- `docs/validation-socle-2026-10-18.md` : seul fichier nouveau à versionner éventuellement ; il contient ce rapport et les commandes.
- `.venv/` : environnement Python local créé et ignoré par Git ; dépendances runtime existantes et pytest installés uniquement ici.
- Aucun fichier applicatif, test existant, manifest, dépendance versionnée, document antérieur ou PDF n'a été modifié.

Les tests passent sans correction de code ; aucune modification d'architecture n'a été entreprise.

## 6. Résumé du diff

Le diff des fichiers suivis est vide ; `git diff --check` réussit. Le présent rapport est un nouveau fichier non suivi. `.venv/` est ignoré. Aucun commit créé.

## 7. Difficultés et limites

- Python système : `C:\Python312\python.exe`, version 3.12.1 ; pip et pytest étaient absents. La création de `.venv` a fourni pip sans modifier Python système.
- Dépendances runtime initialement absentes : annotated-doc, annotated-types, anyio, certifi, click, fastapi, h11, httpcore, httpx, idna, opentelemetry-api, pydantic, pydantic_core, starlette, typing-inspection et uvicorn. typing_extensions était en 4.15.0 au lieu de 4.16.0.
- La première installation restreinte ne trouvait aucun paquet ; l'installation légère avec réseau autorisé a réussi.
- Un avertissement `StarletteDeprecationWarning` indique que TestClient déprécie httpx au profit de httpx2. Les tests passent ; aucune migration de dépendances n'a été faite.
- La configuration par défaut vise Kubernetes. Le démarrage hors cluster demande les overrides locaux existants, notamment `KUBERNETES_CA_FILE=""` et `KUBERNETES_TOKEN_FILE=""`. Les tests les injectent déjà. Le contrôle de démarrage les a injectés directement dans l'environnement Python.
- Une commande de comparaison de versions a échoué à cause de guillemets PowerShell ; elle a été corrigée et a ensuite réussi. Cet échec ne concerne pas la syntaxe des fichiers du projet.
- Validation réalisée avec Python 3.12.1, pas avec l'image Docker Python 3.13. Le comportement des services réels et la qualité du LLM restent hors de ce contrôle.

## 8. Reproduction dans PowerShell

Depuis la racine du dépôt, sans activation de l'environnement virtuel :

```powershell
Set-Location 'C:\Users\anass\OneDrive\Documents\GitHub\kubeAI'
# Seulement si .venv n'existe pas encore :
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check --retries 0 --timeout 15 -r agent/requirements.txt pytest==8.4.2
.\.venv\Scripts\python.exe -m pip check
Push-Location agent
try {
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
    ..\.venv\Scripts\python.exe -B -m pytest tests/test_api.py tests/test_chat.py -v -p no:cacheprovider
} finally {
    Pop-Location
}
```

Cette sélection exclut explicitement les tests live, même si une variable de contexte Kubernetes existe déjà. Les commandes de compilation et de contrôle de démarrage sont reproduites intégralement dans l'annexe.

## 9. Étape suivante proposée

Après validation de ce contrôle, préparer l'intégration ciblée d'Alloy → Mimir, en conservant Loki et les clients/tests existants. Les validations d'ingestion réelles devront se faire sur une machine adaptée. Mistral restera une étape distincte. Arrêt à la fin de l'étape 1.

## Annexe — journal exact des commandes shell

Sauf indication contraire, répertoire de travail : `C:\Users\anass\OneDrive\Documents\GitHub\kubeAI`. Les commandes lancées en parallèle n'impliquent pas un ordre strict dans cette liste. Les répétitions sont signalées.

### 1. État Git initial

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' status --porcelain=v2 --branch
```

Exécutée trois fois : deux sur AnassNDRI, puis sur integration/kubeai. Aucun changement de fichier suivi.

### 2. Branches

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' branch -a -vv
```

Exécutée avant et après fetch ; références inchangées.

### 3. Instructions du dépôt

```powershell
rg --files -g 'AGENTS.md' -g '!**/.git/**'
```

Aucun AGENTS.md trouvé (code retour 1 de rg).

### 4. Inventaire

```powershell
Get-ChildItem -Force -Name
```

Inventaire de la racine.

### 5. Exécutables

```powershell
Get-Command python,pytest,uv -ErrorAction SilentlyContinue | Select-Object Name,Source
```

Python présent ; pytest et uv absents du PATH.

### 6. Synchronisation

```powershell
$env:GIT_TERMINAL_PROMPT='0'
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' -c http.connectTimeout=10 -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 fetch origin
```

Première tentative : accès refusé à .git/FETCH_HEAD dans le bac à sable. Même commande relancée avec autorisation d’accès : succès, code 0.

### 7. Historique

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' log --all --date=iso-strict --format='%h %ad %an %d %s' -10
```

Historique inspecté après fetch.

### 8. Commits distants non intégrés

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' log --format='%h %an %s' AnassNDRI..origin/main AnassNDRI..origin/AnassNDRI AnassNDRI..origin/kevin AnassNDRI..origin/feat/llm-local
```

Sortie vide : aucun commit non intégré parmi ces références.

### 9. Documentation et dépendances

```powershell
Get-Content -LiteralPath 'agent/requirements.txt' -Raw
Get-Content -LiteralPath 'agent/README.md' -Raw
Get-Content -LiteralPath 'agent/Dockerfile' -Raw
```

Lecture seule.

### 10. Python système

```powershell
python -c "import sys, importlib.metadata as m, importlib.util as u; print(sys.executable); print(sys.version); [(print(n, m.version(n) if u.find_spec(n) else 'MISSING')) for n in ['pytest','fastapi','httpx','pydantic','uvicorn']]"
```

Python 3.12.1 ; les cinq distributions recherchées sont absentes.

### 11. Création de branche

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' switch -c integration/kubeai AnassNDRI
```

Succès avec autorisation d’écriture Git ; départ 23260c91b45ed202e6aef61576263d6cbd1cb615.

### 12. Runtime complémentaire

```powershell
& 'C:\Users\anass\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -c "import sys,importlib.metadata as m; print(sys.executable); print(sys.version); names=['pytest','fastapi','httpx','pydantic','uvicorn']; [(print(n,m.version(n))) for n in names]"
```

Python 3.12.14 ; commande arrêtée sur pytest absent. Runtime non utilisé pour les tests.

### 13. pip système

```powershell
python -m pip --version
```

Échec : pip absent du Python système.

### 14. Configuration et fixtures

```powershell
Get-Content -LiteralPath '.env.example' -Raw
Get-Content -LiteralPath '.gitignore' -Raw
Get-Content -LiteralPath 'agent/tests/test_api.py' -TotalCount 70
Get-Content -LiteralPath 'agent/tests/test_chat.py' -TotalCount 80
Get-Content -LiteralPath 'agent/app/config.py' -Raw
Get-Content -LiteralPath 'agent/app/main.py' -Raw
```

Lecture seule.

### 15. Compilation sans bytecode

```powershell
python -c "from pathlib import Path; paths=sorted(p for p in Path('.').rglob('*.py') if not any(part in {'.git','.venv','venv','notes_no_commit'} for part in p.parts)); [compile(p.read_bytes(),str(p),'exec') for p in paths]; print(f'Compilation syntaxique : {len(paths)} fichiers Python valides')"
```

27 fichiers valides.

### 16. Environnement isolé

```powershell
python -m venv .venv
```

Succès ; .venv est ignoré par Git.

### 17. Installation légère

```powershell
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check --retries 0 --timeout 15 -r agent/requirements.txt pytest==8.4.2
```

Première tentative dans le bac à sable : aucun paquet accessible. Même commande avec accès réseau autorisé : succès. Aucun paquet installé dans le Python système.

### 18. Tests unitaires (depuis agent/)

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
..\.venv\Scripts\python.exe -B -m pytest tests/test_api.py tests/test_chat.py -v -p no:cacheprovider
```

27 passed, 1 warning in 12.52s. Aucun test live collecté.

### 19. Cohérence des dépendances

```powershell
.\.venv\Scripts\python.exe -m pip check
```

No broken requirements found.

### 20. Exclusions Git

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' check-ignore .venv/Scripts/python.exe .env .env.example agent/__pycache__/test.pyc .pytest_cache/
```

.venv, .env et caches ignorés ; .env.example conservé.

### 21. Démarrage et configuration (depuis agent/)

```powershell
@'
import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

async def forbid_network(*args, **kwargs):
    raise AssertionError('Unexpected outbound HTTP request')

with patch.dict(os.environ, {}, clear=True):
    try:
        with TestClient(create_app()):
            raise AssertionError('Missing Kubernetes CA should fail startup')
    except FileNotFoundError:
        print('Default cluster configuration: missing projected CA detected as expected')

local_env = {
    'KUBERNETES_CA_FILE': '',
    'KUBERNETES_TOKEN_FILE': '',
    'KUBERNETES_API_URL': 'http://127.0.0.1:8001',
    'PROMETHEUS_URL': 'http://127.0.0.1:9090',
    'LOKI_URL': 'http://127.0.0.1:3100',
    'LLM_BASE_URL': 'http://127.0.0.1:11435/v1',
    'LLM_API_KEY_FILE': '',
}
with patch.dict(os.environ, local_env, clear=True), patch('httpx.AsyncClient.send', forbid_network):
    with TestClient(create_app()) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        schema = client.get('/openapi.json').json()
        required = {'/health', '/ready', '/cluster/status', '/llm/status', '/v1/models', '/v1/chat/completions', '/diagnose'}
        assert required <= set(schema['paths'])
        assert client.get('/v1/models').status_code == 200
        print('Local startup, /health, OpenAPI and model discovery: OK; outbound HTTP forbidden')

invalid = [
    {'namespace': 'Invalid_namespace'},
    {'request_timeout': 0},
    {'metrics_max_age': 1},
    {'llm_timeout': 0},
]
for values in invalid:
    try:
        Settings(**values)
    except ValueError:
        continue
    raise AssertionError(f'Invalid configuration accepted: {values}')
with patch.dict(os.environ, {'UPSTREAM_TIMEOUT_SECONDS': 'invalid'}, clear=True):
    try:
        Settings.from_env()
    except ValueError:
        print('Invalid configuration: namespace and three numeric bounds rejected; invalid numeric environment value rejected')
    else:
        raise AssertionError('Invalid numeric environment value accepted')
'@ | ..\.venv\Scripts\python.exe -B -
```

Code 0 : démarrage local, trois routes et cinq configurations invalides contrôlés ; aucun HTTP sortant.

### 22. Comparaison des versions, première commande

```powershell
.\.venv\Scripts\python.exe -c "from importlib.metadata import version; from pathlib import Path; requirements=[line for line in Path('agent/requirements.txt').read_text().splitlines() if line and not line.startswith('#')]; errors=[(line,version(line.split('==')[0])) for line in requirements if version(line.split('==')[0]) != line.split('==')[1]]; assert not errors,errors; print(f'{len(requirements)} pinned runtime dependencies match; pytest={version(\"pytest\")}')"
```

Échec de la commande de contrôle : guillemets PowerShell incorrects ; aucun fichier Python du projet en cause.

### 23. Comparaison des versions corrigée

```powershell
@'
from importlib.metadata import version
from pathlib import Path
requirements = [line for line in Path('agent/requirements.txt').read_text().splitlines() if line and not line.startswith('#')]
errors = [(line, version(line.split('==')[0])) for line in requirements if version(line.split('==')[0]) != line.split('==')[1]]
assert not errors, errors
print(len(requirements), 'pinned runtime dependencies match; pytest=' + version('pytest'))
'@ | .\.venv\Scripts\python.exe -B -
```

Les 17 versions runtime correspondent exactement au fichier ; pytest 8.4.2.

### Contrôle complémentaire en lecture seule

Les sept commandes suivantes ont été exécutées en parallèle du contrôle principal. Aucun fichier modifié et aucun test exécuté par ce contrôle complémentaire.

Commande 1 :

```powershell
Get-Location; rg --files -g AGENTS.md -g '*requirements*' -g pyproject.toml -g pytest.ini -g .env.example -g .gitignore -g Dockerfile -g 'agent/**'
```

Commande 2 :

```powershell
$paths = @('agent/app/config.py','agent/app/main.py','agent/app/clients/kubernetes_client.py','agent/app/clients/base.py','agent/app/api/routes.py','agent/app/api/chat.py','agent/requirements.txt','agent/Dockerfile','.env.example','.gitignore'); foreach ($p in $paths) { Write-Output $p; $i=0; Get-Content -LiteralPath $p | ForEach-Object { $i++; '{0,4}: {1}' -f $i, $_ } }
```

Commande 3 :

```powershell
$paths = @('agent/tests/test_api.py','agent/tests/test_chat.py','agent/tests/integration/test_live_cluster.py','agent/tests/integration/test_live_chat.py','agent/README.md','agent/app/models.py','agent/app/services/llm_service.py','agent/app/services/diagnosis.py'); foreach ($p in $paths) { Write-Output $p; $i=0; Get-Content -LiteralPath $p | ForEach-Object { $i++; '{0,4}: {1}' -f $i, $_ } }
```

Commande 4 :

```powershell
$paths = @('agent/tests/integration/test_live_cluster.py','agent/tests/integration/test_live_chat.py','agent/app/services/cluster_status.py'); foreach ($p in $paths) { Write-Output $p; $i=0; Get-Content -LiteralPath $p | ForEach-Object { $i++; '{0,4}: {1}' -f $i, $_ } }; rg -n 'ZoneInfo|read_text|write_text|pytest|skip|skipUnless|KUBEAI_LIVE|subprocess|platform|ssl' agent
```

Commande 5 :

```powershell
rg -n '^    def test_' agent/tests/test_api.py agent/tests/test_chat.py agent/tests/integration; Get-Content -LiteralPath agent/app/prompts/system_prompt.txt; Get-Command python,python3,py,pytest -ErrorAction SilentlyContinue | Select-Object Name,Source; rg --files -g '*test*' -g '*.toml' -g '*.ini' -g '*requirements*' -g '*lock*' -g '!notes_no_commit/**'
```

Commande 6 :

```powershell
python -c "import importlib.metadata as m, importlib.util as u, locale, sys; print(sys.version); print('encoding=', locale.getencoding()); names=('pytest','fastapi','httpx','pydantic','uvicorn'); print({n: (m.version(n) if u.find_spec(n) else 'MISSING') for n in names})"
```

Commande 7 :

```powershell
python -c "from importlib import metadata; from pathlib import Path; entries=Path('agent/requirements.txt').read_text().splitlines(); print('\n'.join(name + ': ' + (metadata.version(name) if next(metadata.distributions(name=name), None) else 'MISSING') for name in (entry.split('==')[0] for entry in entries if entry and not entry.startswith('#'))))"
```

### Vérifications Git finales

Exécutées avant création du rapport, puis répétées après sa création :

```powershell
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' diff --check
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' diff --stat
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' status --short --branch
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' rev-parse HEAD
git -c safe.directory='C:/Users/anass/OneDrive/Documents/GitHub/kubeAI' for-each-ref --format='%(refname:short) %(objectname)' refs/heads refs/remotes/origin
```

Avant rapport : arbre propre ; HEAD et AnassNDRI inchangés. Après rapport : seul ce fichier nouveau apparaît ; diff suivi vide.

### Lecture finale du rapport

```powershell
Get-Item -LiteralPath 'docs/validation-socle-2026-10-18.md' | Select-Object FullName,Length
Get-Content -LiteralPath 'docs/validation-socle-2026-10-18.md' -TotalCount 90
```

Le rapport a été relu ; son seul ajout est confirmé dans le statut Git final.
