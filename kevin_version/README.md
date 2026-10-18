# KubeAI — Assistant IA d'observabilité Kubernetes

> Projet de Master MSE — Cloud Services and Systems (HUG)
> Assistant conversationnel pour le monitoring et le troubleshooting de clusters Kubernetes via le langage naturel.

---

## 🎯 Objectifs du projet

Développer et déployer un **assistant IA d'observabilité Kubernetes** sur une infrastructure cloud souveraine :

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────────────┐     ┌─────────────────┐
│  Interface  │────▶│  Agent IA   │────▶│  Observabilité                      │────▶│  Cluster K8s    │
│  Open WebUI │     │  (FastAPI + │     │  • Prometheus / Mimir (métriques)   │     │                 │
│             │◀────│  LangChain) │◀────│  • Loki (logs)                      │◀────│                 │
│             │     │             │     │  • Grafana Alloy (collecte)         │     │                 │
│             │     │             │     │  • Événements Kubernetes            │     │                 │
└─────────────┘     └─────────────┘     └─────────────────────────────────────┘     └─────────────────┘
```

**Fonctionnalités attendues :**
- Poser des questions en langage naturel sur l'état du cluster
- Investiguer des incidents avec chronologie (alertes + métriques + logs)
- Corréler logs et métriques pour formuler des hypothèses de cause

---

## 🏥 Contexte métier : données médicales & souveraineté

Ce projet s'inscrit dans un contexte où les **données de santé sont sensibles** et soumises à des réglementations strictes (RGPD, LPM en Suisse, hébergement HDS). Les choix technologiques reflètent cette contrainte :

| Enjeu | Choix retenu | Justification |
|-------|-------------|---------------|
| **Hébergement cloud** | **Exoscale** (Suisse) | Cloud souverain suisse, datacenters en Suisse, conforme aux exigences de localisation des données de santé |
| **LLM** | **Mistral AI** (modèle open-weight) | Modèle européen (France), open-weight, déployable en local ou sur infrastructure souveraine — pas de dépendance à un fournisseur américain |
| **Stack de monitoring** | **Grafana k8s-monitoring** (Alloy + Loki + Mimir + Grafana) | Stack open-source, déployable on-premise ou sur cloud souverain, pas de vendor lock-in |
| **Interface** | **Open WebUI** | Open-source, auto-hébergeable, compatible avec des LLM locaux ou souverains |
| **Agent IA** | **FastAPI + LangChain** (Python) | Open-source, contrôlable, auditable — essentiel pour la traçabilité des décisions en contexte médical |

> **Principe directeur** : aucune donnée de télémétrie ou de log ne quitte l'infrastructure souveraine. Le LLM peut être déployé localement (Ollama/vLLM) ou via une API souveraine.

---

## 🏗️ Architecture technique

### Composants

| Composant | Technologie | Rôle |
|-----------|-------------|------|
| **Cluster Kubernetes** | Minikube (local) → Exoscale (cloud) | Exécution des workloads |
| **Collecte métriques** | Prometheus / Mimir | Stockage des séries temporelles |
| **Collecte logs** | Grafana Loki | Agrégation et recherche de logs |
| **Collecte unifiée** | Grafana Alloy | Agent de collecte (métriques + logs + traces) |
| **Visualisation** | Grafana | Dashboards de supervision |
| **Interface conversationnelle** | Open WebUI | Interface utilisateur pour poser des questions |
| **Agent IA** | FastAPI + LangChain | Orchestration : traduit les questions en requêtes, agrège les données, génère les réponses |
| **LLM** | Mistral (open-weight) | Génération des réponses en langage naturel |

### Flux de données

```
Question utilisateur (Open WebUI)
        │
        ▼
┌─────────────────┐
│   Agent IA      │  ← FastAPI expose des endpoints REST
│  (LangChain)    │  ← Interprète l'intention de la question
│                 │  ← Sélectionne les outils pertinents
└────────┬────────┘
         │
    ┌────┴────┬──────────┬─────────────┐
    ▼         ▼          ▼             ▼
┌───────┐ ┌───────┐ ┌─────────┐ ┌─────────────┐
│PromQL │ │LogQL  │ │K8s API  │ │  Événements │
│(Mimir)│ │(Loki) │ │(pods,   │ │  K8s        │
│       │ │       │ │nodes)   │ │             │
└───┬───┘ └───┬───┘ └────┬────┘ └──────┬──────┘
    │         │          │             │
    └─────────┴──────────┴─────────────┘
                    │
                    ▼
            ┌─────────────┐
            │  Contexte   │  ← Données brutes agrégées
            │  enrichi    │
            └──────┬──────┘
                   │
                   ▼
            ┌─────────────┐
            │  Mistral    │  ← Génération de la réponse
            │  (LLM)      │    (analyse, diagnostic, recommandations)
            └──────┬──────┘
                   │
                   ▼
            Réponse à l'utilisateur (Open WebUI)
```

### Déploiement Kubernetes

| Namespace | Ressources |
|-----------|-----------|
| `kubeai` | Agent IA (Deployment + Service + ConfigMap + RBAC) |
| `kubeai` | Open WebUI (Deployment + Service) |
| `monitoring` | Prometheus/Mimir, Loki, Grafana, Alloy |

---

## 🛠️ Stack technologique détaillée

| Couche | Technologie | Version envisagée |
|--------|-------------|-------------------|
| Runtime conteneurs | OrbStack (local) / containerd (cloud) | — |
| Orchestrateur | Kubernetes (Minikube → Exoscale) | v1.35.x |
| Gestionnaire de packages | Helm | v3.x |
| Monitoring (métriques) | Grafana Mimir (ou Prometheus) | dernière stable |
| Monitoring (logs) | Grafana Loki | dernière stable |
| Collecte | Grafana Alloy | dernière stable |
| Visualisation | Grafana | dernière stable |
| Interface IA | Open WebUI | dernière stable |
| Agent IA | Python + FastAPI + LangChain | 3.12+ / latest |
| LLM | Mistral (ex: Mistral-7B-Instruct, Mixtral 8x7B) | à affiner selon ressources |
| Déploiement K8s | Manifests YAML + Kustomize | — |

---

## 📋 Plan de développement

### ✅ Étape 1 — Cluster Kubernetes *(en cours)*
- [x] Installer Minikube + OrbStack (Mac Silicon)
- [x] Créer le cluster local avec ressources adaptées
- [x] Activer les addons (metrics-server, ingress)
- [x] Créer la structure de dossiers du projet
- [x] Rédiger le README (ce document)

### 🔲 Étape 2 — Stack de monitoring (k8s-monitoring)
- [ ] Installer Helm
- [ ] Ajouter les repos Helm Grafana
- [ ] Déployer Grafana Alloy (collecte)
- [ ] Déployer Loki (logs)
- [ ] Déployer Mimir (métriques) — ou Prometheus en mode simple
- [ ] Déployer Grafana (visualisation)
- [ ] Vérifier la collecte : métriques + logs visibles dans Grafana
- [ ] Créer un dashboard de santé cluster

### 🔲 Étape 3 — Agent IA (FastAPI + LangChain)
- [ ] Créer l'application FastAPI (structure `agent/app/`)
- [ ] Implémenter les clients : Prometheus/Mimir, Loki, Kubernetes API
- [ ] Implémenter les outils LangChain (query_metrics, query_logs, get_pod_status, get_events)
- [ ] Configurer le LLM Mistral (Ollama local ou API)
- [ ] Définir le system prompt (rôle d'expert observabilité K8s)
- [ ] Créer le Dockerfile de l'agent
- [ ] Écrire les tests unitaires

### 🔲 Étape 4 — Open WebUI + intégration
- [ ] Déployer Open WebUI sur le cluster
- [ ] Configurer Open WebUI pour utiliser l'agent IA comme "tool" / backend custom
- [ ] Tester le flux complet : question → agent → données → réponse

### 🔲 Étape 5 — Déploiement cloud (Exoscale)
- [ ] Créer un cluster Kubernetes sur Exoscale (SKS)
- [ ] Adapter les manifests (LoadBalancer, stockage persistant Exoscale)
- [ ] Déployer la stack complète
- [ ] Configurer l'accès sécurisé (TLS, authentification)

### 🔲 Étape 6 — Tests, démos et livrables
- [ ] Scénario 1 : Santé globale du cluster
- [ ] Scénario 2 : Investigation d'incident avec chronologie
- [ ] Scénario 3 : Corrélation logs/métriques et hypothèses de cause
- [ ] Rapport de tests fonctionnels et de résilience
- [ ] Documentation des limites et plan d'amélioration

---

## 🐛 Journal des erreurs et résolutions (Étape 2 — Monitoring)

### **Environnement de travail**
- **OS** : macOS Darwin 26.5.2 (arm64 / Apple Silicon)
- **Runtime conteneurs** : OrbStack 2.2.4 (remplace Docker Desktop)
- **Cluster** : Minikube v1.38.1 avec driver Docker, Kubernetes v1.35.1
- **Helm** : v4.3.0
- **Ressources Minikube** : 4 CPUs, 7168 MB RAM, 20 GB disque

---

### **Erreur 1 : Minikube — Mémoire insuffisante**

| | |
|---|---|
| **Commande** | `minikube start --driver=docker --cpus=4 --memory=8192 --disk-size=20g` |
| **Erreur** | `Exiting due to MK_USAGE: Docker Desktop has only 8003MB memory but you specified 8192MB` |
| **Cause** | OrbStack/Minikube détecte ~8003 MB disponibles, la demande de 8192 MB dépasse cette limite |
| **Résolution** | Réduire la mémoire à `7168` MB (7 GB) |
| **Commande corrigée** | `minikube start --driver=docker --cpus=4 --memory=7168 --disk-size=20g` |
| **Statut** | ✅ Résolu |

---

### **Erreur 2 : Loki — Stockage objet requis en mode scalable**

| | |
|---|---|
| **Commande** | `helm install loki grafana/loki --namespace monitoring --values k8s/monitoring/loki-values.yaml` |
| **Erreur** | `Cannot run scalable targets (backend, read, write) or distributed targets without an object storage backend` |
| **Cause** | Le chart Loki par défaut tente de déployer en mode distribué (backend/read/write séparés), ce qui nécessite un stockage objet (Minio/S3). Or le cahier des charges demande **"Mimir (no minio)"** — pas de Minio. |
| **Tentatives** | Configuration initiale avec `deploymentMode: Distributed` |
| **Résolution** | Passer en mode **SingleBinary** (monolithique) avec stockage filesystem local |
| **Configuration appliquée** | `deploymentMode: SingleBinary`, `loki.storage.type: filesystem`, désactivation de `backend`, `read`, `write` |
| **Statut** | ✅ Résolu — Loki déployé en mode single binary |

---

### **Erreur 3 : Mimir — Chart `grafana/mimir` inexistant**

| | |
|---|---|
| **Commande** | `helm install mimir grafana/mimir --namespace monitoring --values k8s/monitoring/mimir-values.yaml` |
| **Erreur** | `chart "mimir" matching not found in grafana index` |
| **Cause** | Le chart simple `grafana/mimir` n'existe pas. Seuls existent : `grafana/mimir-distributed` et `grafana/mimir-openshift-experimental` |
| **Vérification** | `helm search repo grafana/mimir` → retourne uniquement `mimir-distributed` (v6.2.1) et `mimir-openshift-experimental` (v2.1.0) |
| **Décision** | Abandon du déploiement via Helm pour Mimir. Passage à un **déploiement manuel Kubernetes** (Deployment + Service + PVC + ConfigMap) pour plus de contrôle |
| **Statut** | ⚠️ En cours — Déploiement manuel en cours de test |

---

### **Erreur 4 : Mimir — Chart `mimir-distributed` déploie Minio/Kafka par défaut**

| | |
|---|---|
| **Commande** | `helm install mimir grafana/mimir-distributed --namespace monitoring --values k8s/monitoring/mimir-values.yaml` |
| **Problème** | Même avec `minio.enabled: false` et `kafka.enabled: false`, le chart déploie des composants indésirables : `mimir-minio`, `mimir-kafka`, `mimir-alertmanager` (CrashLoopBackOff), `mimir-overrides-exporter` (CrashLoopBackOff), `mimir-make-minio-buckets` |
| **Cause** | Le chart `mimir-distributed` est conçu pour un déploiement distribué complet. La désactivation de Minio/Kafka ne suffit pas à obtenir un mode monolithique propre. |
| **Conséquence** | Pods en `CrashLoopBackOff`, ressources gaspillées, complexité inutile pour un environnement local Minikube |
| **Décision** | Abandon définitif du chart Helm `mimir-distributed` au profit d'un **manifest Kubernetes manuel** |
| **Statut** | ⚠️ En cours — Voir Erreur 5 |

---

### **Erreur 5 : Mimir (manuel) — Champs de configuration invalides (Mimir 3.x)**

| | |
|---|---|
| **Fichier** | `k8s/monitoring/mimir-deployment.yaml` (ConfigMap `mimir-config`) |
| **Erreur 1** | `field ingestion_rate_mb not found in type validation.plainLimits` |
| **Erreur 2** | `field ingestion_rate_strategy not found in type validation.plainLimits` |
| **Erreur 3** | `field analytics not found in type mimir.Config` |
| **Erreur 4** | `mapping key "multitenancy_enabled" already defined at line 5` |
| **Erreur 5** | `mapping key "compactor" already defined at line 18` |
| **Cause** | La structure de configuration de Mimir a changé entre les versions. Les champs `ingestion_rate_mb`, `ingestion_burst_size_mb`, `ingestion_rate_strategy` et `analytics` n'existent plus ou ont été renommés dans Mimir 3.2.1. De plus, des clés YAML étaient dupliquées dans le fichier. |
| **Tentatives** | 1. Suppression des champs `ingestion_rate_mb` et `ingestion_burst_size_mb`<br>2. Suppression de `ingestion_rate_strategy`<br>3. Suppression de `analytics`<br>4. Correction des clés dupliquées (`multitenancy_enabled`, `compactor`) |
| **Piste actuelle** | Configuration **minimale** sans section `limits`, `usage_stats`, `analytics` — laisser Mimir utiliser ses valeurs par défaut |
| **Résolution** | 1. Renommer les champs `limits` au format Mimir : `ingestion_rate` (samples/s) et `ingestion_burst_size` (samples) — les champs `*_mb` et `ingestion_rate_strategy` sont des champs Loki/Cortex inexistants dans Mimir<br>2. Supprimer la section `analytics` (remplacée par `usage_stats` déjà présent)<br>3. Passer `target: all,alertmanager` → `target: all` et ajouter `ruler_storage` + `alertmanager_storage` en filesystem (sinon ruler/alertmanager crashent faute de backend) |
| **Statut** | ✅ Résolu — Mimir `1/1 Running`, log `Application started`, tous les modules ACTIVE dans le ring |

---

### **Erreur 6 : Loki — Pod `loki-chunks-cache-0` en Pending**

| | |
|---|---|
| **Symptôme** | `loki-chunks-cache-0` reste en `Pending` (0/2) depuis le déploiement |
| **Cause probable** | Ressources insuffisantes ou PVC non disponible pour le cache |
| **Impact** | Faible — le cache est optionnel en mode SingleBinary, Loki fonctionne sans |
| **Piste** | Vérifier `kubectl describe pod loki-chunks-cache-0 -n monitoring` pour voir l'événement de scheduling |
| **Statut** | ⚠️ Non bloquant — à investiguer si nécessaire |

---

### **Erreur 7 : Alloy — Conflit de ServiceAccount entre kubectl et Helm**

| | |
|---|---|
| **Commande** | `kubectl apply -f k8s/monitoring/alloy-rbac.yaml` puis `helm install alloy grafana/alloy ...` |
| **Erreur** | `ServiceAccount "alloy" in namespace "monitoring" exists and cannot be imported into the current release: invalid ownership metadata; label validation error: missing key "app.kubernetes.io/managed-by": must be set to "Helm"` |
| **Cause** | Le ServiceAccount `alloy` a été créé manuellement via `kubectl apply` (dans `alloy-rbac.yaml`), puis Helm tente de le recréer via son chart. Helm refuse d'adopter une ressource existante qui n'a pas ses labels/annotations de ownership (`app.kubernetes.io/managed-by: Helm`, `meta.helm.sh/release-name`, `meta.helm.sh/release-namespace`). |
| **Tentative 1** | `kubectl delete serviceaccount alloy -n monitoring` → résout le ServiceAccount, mais le même problème se produit avec le `ClusterRole` |
| **Erreur 2** | `ClusterRole "alloy" in namespace "" exists and cannot be imported into the current release: invalid ownership metadata` |
| **Cause 2** | Même problème : le `ClusterRole` et le `ClusterRoleBinding` créés manuellement entrent en conflit avec ceux générés par le chart Helm Alloy (qui crée ses propres ressources RBAC via `rbac.create: true`). |
| **Analyse** | Le chart Helm `grafana/alloy` crée **déjà** son propre ServiceAccount, ClusterRole et ClusterRoleBinding quand `rbac.create: true` (valeur par défaut). Le fichier `alloy-rbac.yaml` manuel est donc **redondant** et crée un conflit. |
| **Hypothèses de résolution** | **Option A (recommandée)** : Supprimer complètement `alloy-rbac.yaml` et laisser Helm gérer tout le RBAC via `rbac.create: true` dans `alloy-values.yaml`. Vérifier que les permissions par défaut du chart sont suffisantes (lecture pods, nodes, logs).<br><br>**Option B** : Garder le RBAC manuel mais désactiver la création RBAC dans Helm (`rbac.create: false` et `serviceAccount.create: false` dans `alloy-values.yaml`). Nécessite de s'assurer que le ServiceAccount manuel a le bon nom (`alloy`) et les bonnes permissions.<br><br>**Option C** : Utiliser `helm install --take-ownership` pour forcer Helm à adopter les ressources existantes (disponible depuis Helm 3.14+ / Helm 4.x). |
| **Statut** | 🔄 En cours — Option A à tester en priorité |

---

### **Récapitulatif de l'état actuel (15 oct. 2026)**

| Composant | Statut | Mode | Notes |
|-----------|--------|------|-------|
| **Minikube** | ✅ Running | Docker driver | 4 CPUs, 7 GB RAM |
| **Loki** | ✅ Running | SingleBinary (Helm) | Stockage filesystem, pas de Minio |
| **Mimir** | ✅ Running | Manuel (K8s manifests) | Mode monolithique `target: all`, stockage filesystem, config corrigée (voir Erreur 5) |
| **Alloy** | 🔄 En cours | Helm | Conflit RBAC kubectl/Helm (voir Erreur 7) — Option A à tester |
| **Grafana** | ⏳ À déployer | — | Sera déployé après Alloy |

---

### **Pistes pour la suite**

1. **Alloy (Erreur 7)** : Tester l'**Option A** — supprimer `alloy-rbac.yaml`, nettoyer les ressources RBAC existantes, et laisser Helm gérer le RBAC via `rbac.create: true`. Si les permissions par défaut sont insuffisantes, les ajuster dans `alloy-values.yaml` via `rbac.rules`.
2. **Grafana** : Préparer le fichier `grafana-values.yaml` avec les datasources Mimir (`http://mimir.monitoring.svc:9009/prometheus`) et Loki (`http://loki-gateway.monitoring.svc:80`) préconfigurées.
3. **Vérification end-to-end** : Une fois Alloy déployé, vérifier que les métriques arrivent dans Mimir (`up`, `container_cpu_usage_seconds_total`) et que les logs arrivent dans Loki (`{namespace="monitoring"}`).
4. **Nettoyage** : Supprimer le PVC `storage-loki-0` en `Terminating` si nécessaire (`kubectl delete pvc storage-loki-0 -n monitoring --force --grace-period=0`).

---

## 📅 Livrables attendus (rappel du sujet)

| # | Livrable | Échéance |
|---|----------|----------|
| 1 | Documentation des exigences et de l'architecture | — |
| 2 | Environnement Kubernetes observable | — |
| 3 | Assistant IA de monitoring | — |
| 4 | Rapport de tests fonctionnels et de résilience | — |
| 5 | Démonstration de scénarios de surveillance et diagnostic | **3 novembre 2026, 8h45** |

---

## 🚀 Démarrage rapide (local)

```bash
# 1. Démarrer le cluster
minikube start --driver=docker --cpus=4 --memory=7168 --disk-size=20g

# 2. Activer les addons
minikube addons enable metrics-server ingress

# 3. Vérifier
kubectl get nodes

# 4. Déployer la stack de monitoring (à venir)
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
# ... (commandes détaillées à venir)

# 5. Accéder à Grafana / Open WebUI
minikube service <service-name> -n <namespace>
```

---

## 📁 Structure du projet

```
kevin_version/
├── README.md                 # Ce document
├── docs/                     # Documentation détaillée
│   ├── architecture.md       # Schémas et décisions d'architecture
│   ├── api-contract.md       # Contrat API de l'agent IA
│   ├── decisions.md          # Journal des décisions techniques
│   └── demo-scenarios.md     # Scénarios de démonstration
├── k8s/                      # Manifests Kubernetes
│   ├── namespace.yaml        # Namespace kubeai + monitoring
│   ├── monitoring/           # Helm values + manifests monitoring
│   │   ├── alloy-values.yaml
│   │   ├── loki-values.yaml
│   │   ├── mimir-values.yaml
│   │   └── grafana-values.yaml
│   ├── agent/                # Déploiement de l'agent IA
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── configmap.yaml
│   │   └── rbac.yaml
│   └── open-webui/           # Déploiement Open WebUI
│       ├── deployment.yaml
│       └── service.yaml
├── agent/                    # Code source de l'agent IA
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # Point d'entrée FastAPI
│   │   ├── config.py         # Configuration (env vars)
│   │   ├── models.py         # Schémas Pydantic
│   │   ├── api/
│   │   │   └── routes.py     # Endpoints REST
│   │   ├── clients/          # Clients Prometheus, Loki, K8s
│   │   ├── services/         # Logique métier (diagnosis, correlation)
│   │   ├── tools/            # Outils LangChain
│   │   └── prompts/          # System prompts
│   └── tests/
│       ├── unit/
│       └── integration/
├── scripts/                  # Scripts utilitaires
│   ├── start-local.sh
│   ├── stop-local.sh
│   └── validate-local.sh
└── .env.example              # Template des variables d'environnement
```

---

## 🔐 Considérations sécurité & conformité

| Aspect | Mesure |
|--------|--------|
| Données en transit | TLS partout (Ingress + certificats) |
| Données au repos | Chiffrement des volumes persistants (Exoscale) |
| Accès API | RBAC Kubernetes strict (principe du moindre privilège) |
| Secrets | Kubernetes Secrets + chiffrement etcd |
| LLM | Déploiement local ou sur infrastructure souveraine — pas d'envoi de données vers des API tierces non souveraines |
| Audit | Logs d'accès à l'agent IA, traçabilité des requêtes |

---

## 👥 Équipe

| Membre | Rôle |
|--------|------|
| Kevin | Développement agent IA, déploiement, intégration |
| Vincent | ... |
| À compléter | ... |

---

## 📚 Références

- [Grafana k8s-monitoring Helm Chart](https://github.com/grafana/k8s-monitoring-helm)
- [Mistral AI — Modèles open-weight](https://mistral.ai)
- [Exoscale — Cloud souverain suisse](https://www.exoscale.com)
- [Open WebUI](https://github.com/open-webui/open-webui)
- [LangChain](https://python.langchain.com)
- Sujet du projet : `Project5-Presentation.pdf`

---

*Dernière mise à jour : 15 octobre 2026*
