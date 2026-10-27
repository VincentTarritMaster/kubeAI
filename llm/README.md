# kubeAI — profil LLM local facultatif

Le même backend FastAPI sert les deux profils : `mistral-api` par défaut et
`local` pour tout serveur compatible avec l'API OpenAI. L'ancien nom
`LLM_PROVIDER=apertus` reste accepté. Open WebUI garde son endpoint vers l'agent
et le même identifiant virtuel `kubeai` dans les deux cas. Ce profil local
reste une option locale du projet commun.

Le profil local utilise **Apertus 8B Instruct 2509, Q4_K_M**, exécuté par
**llama.cpp b11669** directement sur macOS Apple Silicon avec Metal.
Le modèle suisse et l'inférence restent sur la machine ; aucune API cloud
ni abonnement ne sont nécessaires.

```text
Navigateur → Open WebUI (Kubernetes) → Agent kubeAI (Kubernetes)
                                      ├─ Kubernetes : état des pods
                                      ├─ Mimir : CPU/mémoire
                                      ├─ Loki : logs récents
                                      └─ Apertus / llama.cpp (Mac, GPU Metal)
```

À chaque question, l'agent récupère de nouvelles observations et les transmet
au fournisseur configuré avec la conversation. L'intégration utilise le modèle
virtuel `kubeai` ; l'alias `kubeai-apertus` reste accepté pour l'historique.
Les routes compatibles OpenAI de l'agent ne dépendent
pas d'appels d'outils choisis par le modèle. Le JSON `/cluster/status` reste
accessible séparément. Les réponses sont diffusées en SSE et comportent un
pied de page avec le périmètre, l'heure d'observation et les sources disponibles.

## Ressources et provenance

| Élément | Choix |
|---|---|
| Poids d'origine | `swiss-ai/Apertus-8B-Instruct-2509` |
| Conversion quantifiée | `unsloth/Apertus-8B-Instruct-2509-GGUF`, Q4_K_M |
| Téléchargement du modèle | 5 057 885 440 octets, soit 5,06 Go / 4,71 Gio |
| Runtime macOS ARM64 | Archive officielle llama.cpp, environ 12 Mo |
| Contexte local | 8 192 tokens, une génération simultanée |
| Mémoire estimée | Environ 7–10 Go selon le contexte et les buffers |
| Réponse | 512 tokens par défaut, maximum 1 024 |

Les URL pointent vers une révision/version précise. Les SHA-256 du moteur et
du modèle sont enregistrés dans `local-apertus.json` et vérifiés après téléchargement.
La quantification est une conversion communautaire Unsloth des poids Swiss AI,
pas une distribution GGUF publiée directement par Swiss AI.

Les fichiers sont dans `.cache/apertus/`, exclu de Git. Le script de téléchargement
reprend les transferts interrompus, utilise quatre connexions pour le modèle et
ne télécharge pas les poids dans le disque Docker. Les sauvegardes Open WebUI se
trouvent séparément dans `.cache/webui-backups/`.

## Préparation explicite du profil local

Prérequis pour le profil Apertus existant : macOS Apple Silicon, environ 6 Gio de disque libres, Docker Desktop,
Minikube, kubectl, Python 3 et curl. L'agent dans l'image utilise Python 3.13.
Depuis la racine du dépôt :

```bash
python3 scripts/local-llm.py setup
python3 scripts/local-llm.py start
```

Ces commandes téléchargent/vérifient les poids, démarrent le moteur et transfèrent
la clé locale vers le Secret Kubernetes `kubeai-llm`. La première commande
télécharge environ 5 Go. `start.sh` ne télécharge et ne lance pas le modèle.

Dans le `.env` à la racine, configurer :

```dotenv
LLM_PROVIDER=local
LLM_BASE_URL=http://127.0.0.1:11435/v1
LLM_MODEL=apertus-8b
LLM_API_KEY_FILE=.cache/apertus/api-key
LLM_TIMEOUT_SECONDS=180
```

Le serveur Apertus requiert cette clé. Ouvrir Docker Desktop puis lancer :

Pour déployer après cette préparation explicite :

```bash
bash start.sh
```

Le lanceur valide le `.env`, crée ou démarre les deux clusters Minikube,
construit l'agent et déploie les collecteurs et les services dans leurs clusters
respectifs. Voir le [graphique de la topologie](../docs/two-clusters.md).
Il transfère automatiquement la clé du moteur dans le cluster de supervision.
L'adresse `127.0.0.1` devient `host.docker.internal` pour les pods et le chemin
de clé devient `/var/run/secrets/kubeai-llm/api-key`.

## Utiliser le chat

```bash
# Le tunnel vers Open WebUI est ouvert automatiquement par bash start.sh
```

Ouvrir <http://127.0.0.1:8080>, choisir **kubeai** puis poser :

> Quel est l'état du namespace applications ? Quels pods ont un problème ?

Ou :

> Donne la mémoire utilisée par chaque pod et résume les logs récents disponibles.

Le backend Open WebUI est préconfiguré avec
`http://kubeai-agent.kubeai.svc.cluster.local:8000/v1`. Sa clé `kubeai-local`
est une valeur de compatibilité locale ; elle n'est pas la clé du moteur.
Les préférences de connexion viennent des manifests (`ENABLE_PERSISTENT_CONFIG=False`).
Les générations automatiques de titres/tags/suggestions sont désactivées pour
réserver l'inférence aux questions de l'utilisateur.

Les conversations et fichiers d'Open WebUI sont persistants sur le PVC
`open-webui-data` de 1 Gio. La migration effectue une sauvegarde SQLite cohérente
et une copie des fichiers existants avant de remplacer l'ancien pod.

## Commandes du moteur

```bash
python3 scripts/local-llm.py status
python3 scripts/local-llm.py stop
python3 scripts/local-llm.py start
```

`stop` arrête uniquement le processus du projet, après vérification de sa commande.
Les poids restent sur disque. Le moteur est un processus local détaché, pas un
service macOS installé au démarrage. Après redémarrage du Mac, relancer
explicitement `python3 scripts/local-llm.py start` si ce profil est souhaité.
Arrêter le moteur libère ses ressources de calcul ; le monitoring reste utilisable.
Son journal est `.cache/apertus/llama-server.log`.

Le serveur écoute sur le port 11435 pour être joignable depuis Docker/Minikube,
avec une clé aléatoire obligatoire pour les appels d'inférence. La clé est dans
`.cache/apertus/api-key` (permissions 600) et dans le Secret `kubeai-llm`. Elle n'est
jamais incluse dans Git, les arguments du processus ou l'interface Open WebUI.
Le endpoint natif `/health` est public, conformément au fonctionnement de llama.cpp.
Les pods joignent le Mac via `host.docker.internal`, vérifié sur Minikube/Docker Desktop.

## Tester sans navigateur

Avec un tunnel vers l'agent :

```bash
kubectl --context kubeai-supervision port-forward -n kubeai service/kubeai-agent 8000:8000
```

Dans un autre terminal :

```bash
curl http://127.0.0.1:8000/llm/status
curl http://127.0.0.1:8000/v1/models
curl http://127.0.0.1:8000/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"question":"Quels pods ont un problème ?"}'
```

`/diagnose` retourne le texte et les observations qui ont été transmises au modèle.
`/v1/chat/completions` accepte la conversation et `stream: true` pour Open WebUI.

Tests reproductibles du moteur réel, sans navigateur :

```bash
KUBEAI_LIVE_TEST_CONTEXT=kubeai-supervision python3 agent/tests/integration/test_live_chat.py
```

Le 18 octobre 2026, les tests ont validé un diagnostic sur les pods réels et le
streaming SSE. Les 22 tests automatisés avec dépendances simulées passent également.
Le serveur a été vérifié comme refusant les appels sans clé (HTTP 401).
L'interface n'a pas été contrôlée visuellement, l'accès navigateur ayant été refusé.

## Périmètre et limites

Le namespace surveillé est `applications` par défaut, configurable via `KUBERNETES_NAMESPACE`. Le contexte envoyé comprend jusqu'à 20 pods,
incidents en priorité, et 12 lignes de logs des dix dernières minutes, issues
d'au plus six pods. Les lignes sont limitées à 400 caractères. Ces extraits ne
constituent pas une recherche exhaustive ou une chronologie complète.
L'historique est limité à 32 messages et 16 000 caractères. Un contexte dépassant
la capacité du modèle peut nécessiter une nouvelle conversation plus courte.

L'agent ne modifie aucun workload. Les logs sont traités comme des données ; le
prompt demande au modèle de distinguer observations et hypothèses. Ces consignes
ne garantissent pas l'exactitude des réponses : les preuves restent accessibles
dans `/diagnose`. Les événements Kubernetes et l'investigation historique sont
intégrés ; les traces distribuées et Tempo restent hors périmètre.

Le déploiement actif active l'authentification Open WebUI (`WEBUI_AUTH=True`,
rôle `pending` par défaut) et garde les services internes sur un cluster local de
confiance. Pour le cloud, prévoir TLS, politiques réseau et un contrôle d'accès
adapté. Le modèle local est sur le Mac, pas dans Kubernetes.
Une autre machine peut héberger un serveur compatible et remplacer `LLM_BASE_URL`,
`LLM_MODEL` et le Secret, sans changer la connexion Open WebUI à l'agent.

## Sources

- [Poids Swiss AI officiels](https://huggingface.co/swiss-ai/Apertus-8B-Instruct-2509)
- [Conversion GGUF Unsloth](https://huggingface.co/unsloth/Apertus-8B-Instruct-2509-GGUF)
- [Serveur llama.cpp officiel](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [Connexions compatibles OpenAI dans Open WebUI](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/)
