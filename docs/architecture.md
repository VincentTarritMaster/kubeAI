# Architecture actuelle

Cette page décrit l’architecture **réellement déployée et validée** au
22 octobre 2026. Le document de conception initial est archivé :
[`archive/Group-17-Architecture-initiale.pdf`](archive/Group-17-Architecture-initiale.pdf).

## Vue d’ensemble

KubeAI est un assistant d’observabilité Kubernetes en lecture seule :

```text
                         Open WebUI
                    modèle virtuel "kubeai"
                             |
                             v
                    FastAPI kubeai-agent
             /               |                \
            v                v                 v
   Kubernetes API        Mimir (PromQL)     Loki (LogQL)
   nodes/pods/events     métriques          logs
            ^                ^                 ^
            |                |                 |
            +-------- Alloy -+-----------------+
                      |  logs des pods kubeai
                      |  scrape KSM (15 s)
                      |  scrape pods annotés (30 s)
                      |  scrape cAdvisor via le proxy API (30 s)
                      |
              Mistral API (défaut) ou endpoint local Apertus
```

Le LLM ne reçoit pas d’accès direct au cluster. Le backend prépare une
observation bornée, calcule les faits, puis le LLM peut seulement signaler une
priorité parmi les hypothèses déjà calculées.

## Deux modes de déploiement

### Mode validé : Windows / Docker Desktop

- FastAPI tourne **sur Windows**, avec un kubeconfig de lecture seule
  (`~/.kube/kubeai-readonly.yaml`).
- Open WebUI et la pile de monitoring tournent **dans Kubernetes**.
- `scripts/start.ps1` prépare le namespace, le monitoring, le RBAC de l’agent,
  les Secrets et les tunnels Mimir/Loki, puis démarre FastAPI et ouvre le
  navigateur.
- `scripts/stop.ps1` arrête les processus suivis et met Open WebUI à zéro
  réplica, sans supprimer les PVC ni le pod de démonstration.

C’est le mode utilisé pour les démonstrations A/B/C et les validations réelles.

### Mode Kubernetes complet

- `start.sh` construit l’image de l’agent puis applique `kubectl apply -k k8s/`.
- L’agent est un `Deployment` dans `kubeai`, exposé par le Service
  `kubeai-agent:8000`.
- Open WebUI utilise l’URL interne
  `http://kubeai-agent.kubeai.svc.cluster.local:8000/v1`.
- Ce mode est décrit dans [`agent/README.md`](../agent/README.md) et
  [`k8s/monitoring/README.md`](../k8s/monitoring/README.md).

### Cloud

Aucun déploiement cloud n’est réalisé à ce jour. La cible et les manifests
cloud restent à définir ; voir [`decisions.md`](decisions.md).

## Composants

| Composant | Version / état | Rôle |
|---|---|---|
| Open WebUI | `v0.11.4-slim`, PVC 1 Gio | Interface authentifiée, modèle `kubeai` |
| FastAPI `kubeai-agent` | 0.3.0 | API OpenAI-compatible, diagnostic, lecture seule |
| Kubernetes | Docker Desktop, nœud `desktop-control-plane` | Exécute les composants et le pod de démonstration |
| Alloy | 1.20.1, 1 réplique, PVC 1 Gio | Logs et métriques vers Loki/Mimir |
| Mimir | 3.2.1 monolithique, PVC 10 Gio | Stockage PromQL, rétention `0s` |
| Loki | 3.7.8, PVC 2 Gio | Logs, rétention 30 jours |
| kube-state-metrics | 2.20.0 | État des pods, nœuds et workloads |
| cAdvisor | intégré au kubelet | CPU/mémoire des conteneurs, via le proxy API |
| Mistral API | `mistral-small-2603` | Fournisseur LLM par défaut |
| Apertus | 8B Q4_K_M, optionnel | Endpoint local compatible OpenAI |

## Flux de collecte

- Alloy découvre les pods du namespace `kubeai`.
- Il envoie les logs vers Loki.
- Il scrape kube-state-metrics toutes les 15 s.
- Il scrape les pods annotés `prometheus.io/scrape: "true"` toutes les 30 s.
- Il scrape cAdvisor via le proxy de l’API Kubernetes, job
  `kubernetes-cadvisor`, toutes les 30 s.
- Mimir reçoit les métriques sur `/api/v1/push` ; FastAPI lit l’API PromQL sous
  le préfixe `/prometheus`.

Le pod de démonstration expose aussi `http_requests_total` et
`http_request_duration_seconds` ; il s’auto-génère du trafic `/work`.

## Flux d’une question

1. Open WebUI appelle `/v1/chat/completions` de FastAPI.
2. FastAPI découpe la question en intention : santé A, incident B, corrélation C,
   chat général ou hors périmètre.
3. Il lit Kubernetes, Mimir et Loki avec des bornes strictes.
4. Il calcule les faits, la chronologie et les hypothèses.
5. Il peut appeler Mistral/Apertus pour **signaler** une priorité, jamais pour
   inventer un fait.
6. Il renvoie une réponse JSON ou SSE avec les preuves et les sources.

## RBAC et sécurité

- **Agent** : `ClusterRole` avec `list` uniquement sur `nodes`, `pods` et
  `events`. Aucun Secret, `exec` ou verbe de modification.
- **Alloy** : Role dans `kubeai` pour les pods et `pods/log` ; ClusterRole
  `kubeai-alloy-cadvisor` avec `list/watch` sur `nodes` et `get` sur
  `nodes/proxy`.
- **cAdvisor** : le droit `nodes/proxy` est plus large qu’un simple accès
  métriques, car il passe par le proxy kubelet. Il reste en lecture seule ; le
  collecteur n’est ni privilégié ni monté sur l’hôte.
- **API** : authentification Bearer sur les routes de chat/diagnostic ; échec
  fermé si `KUBEAI_API_KEY` manque.
- **Données** : redaction best-effort avant envoi au LLM ; ce n’est pas une
  anonymisation.
- Aucune remédiation automatique.

## Stockage et limites

- Mimir : PVC 10 Gio, pas de purge automatique (`0s`).
- Loki : PVC 2 Gio, rétention 30 jours.
- Alloy : PVC 1 Gio pour le WAL.
- Open WebUI : PVC 1 Gio.
- Les événements Kubernetes expirent plus vite que les logs et les métriques.
- Le poste Windows de 8 Go est serré ; les collègues disposant de 32 Go peuvent
  valider plus confortablement.
- Pas de Grafana, pas d’Alertmanager, pas de Tempo ; les traces sont hors
  périmètre et les alertes restent à intégrer.

## État de validation

- cAdvisor : `up{job="kubernetes-cadvisor"} = 1` sur Docker Desktop.
- `/cluster/status` : `data_status: complete`, CPU/mémoire non nuls.
- A/B/C : validés avec preuves réelles (logs, Mimir, Loki).
- Tests : 359 tests automatisés + 6 tests HTTP loopback.
- Détails : [`validation-demo-http-2026-10-22.md`](validation-demo-http-2026-10-22.md),
  [`metrics.md`](metrics.md), [`demo-scenarios.md`](demo-scenarios.md).
