| Nom de l’étudiant | Identifiant GitHub | Responsabilité |
| --- | --- | --- |
| À compléter | À compléter | À compléter |
| À compléter | À compléter | À compléter |
| À compléter | À compléter | À compléter |

Document initial de travail. Les choix techniques, le périmètre détaillé et la répartition seront validés collectivement.

# KubeAI — Monitoring Kubernetes clusters with AI

## But du projet

Créer et déployer sur une infrastructure cloud un assistant conversationnel d’observabilité Kubernetes. Une question en langage naturel doit conduire à des requêtes sur les données du cluster, puis à une analyse compréhensible et étayée pour aider à suivre son état et à enquêter sur un incident.

## Exigences du professeur

Le projet 5 (pages 18 à 21 du document de référence) demande de :

- disposer d’un cluster Kubernetes et d’une pile de supervision Prometheus/Mimir et Loki ; collecter et interroger des métriques et des logs, et exploiter les événements utiles à l’analyse ;
- choisir un LLM, intégrer Open WebUI comme interface conversationnelle et concevoir un agent qui orchestre les outils et structure ses réponses ;
- traduire les questions en requêtes, récupérer les données pertinentes et produire une analyse intelligible ;
- évaluer l’exactitude, la fiabilité, la sécurité et les limites des réponses de l’IA ;
- réaliser les trois démonstrations et les livrables indiqués ci-dessous.

Le sujet propose `k8s-monitoring` avec Alloy pour la collecte ; il ne l’impose pas. Le bilan des limites, le plan d’amélioration priorisé et d’éventuelles améliorations constituent la phase 3 facultative, selon le temps disponible.

## Trois démonstrations attendues

1. **Santé globale du cluster** : répondre à une question sur l’état général du cluster à partir de ses signaux d’observabilité.
2. **Investigation d’incident avec chronologie** : rapprocher alertes, métriques et logs pour décrire ce qui arrive à un nœud, un pod ou un service, dans l’ordre des événements.
3. **Corrélation logs et métriques** : formuler des hypothèses de cause, appuyées par les données et distinguées des faits établis.

Les incidents précis, questions de démonstration et critères chiffrés figurant dans la fiche de réunion sont des propositions à valider, pas des exigences supplémentaires du professeur.

## Périmètre et décisions ouvertes

Les **traces distribuées** sont exclues du périmètre du sujet. La **remédiation automatique** reste une question ouverte dans le PDF : le groupe doit décider si l’assistant se limite à des recommandations ou peut agir, après examen des droits et des risques. Aucun mécanisme de remédiation n’est retenu à ce stade.

Minikube avec le pilote Docker est l’environnement local commun du groupe. Le fournisseur cloud, le LLM, la pile exacte de collecte, les seuils, les scénarios et les critères de validation restent à décider collectivement. Exoscale a été proposé comme fournisseur cloud dans les échanges du groupe ; ce choix ne constitue pas une exigence du professeur.

## Livrables et échéance

Le sujet demande :

1. une documentation ou présentation des exigences et de l’architecture ;
2. un environnement Kubernetes observable ;
3. un assistant IA de monitoring ;
4. un rapport de tests fonctionnels et de résilience ;
5. une démonstration de scénarios de surveillance et de diagnostic.

**Rendu : 3 novembre 2026 à 8 h 45.**

## Configuration locale

Prérequis : Docker Desktop, Minikube et kubectl. Depuis la racine du dépôt, démarre Minikube puis vérifie que tu utilises le bon cluster :

    minikube start --driver=docker
    kubectl config current-context
    kubectl get nodes

Le contexte attendu est minikube et le nœud doit être Ready. Applique ensuite les manifests du dossier k8s et vérifie le pod :

    kubectl apply -k .\k8s\
    kubectl get pods -n kubeai

Quand le pod Open WebUI est Running, ouvre l’interface :

    minikube service open-webui -n kubeai --url

Garde Minikube en marche pendant l’utilisation de l’adresse affichée. Le script Bash ./start.sh applique les manifests, mais ne démarre pas Minikube.

## Validation locale

### Démarrer Docker et Minikube

Depuis un terminal PowerShell :

```powershell
docker version
docker ps

minikube start --driver=docker
```

### Vérifier Kubernetes

```powershell
kubectl config current-context
minikube status
kubectl get nodes
```

Résultat attendu :

- contexte : `minikube` ;
- `host` : `Running` ;
- `kubelet` : `Running` ;
- `apiserver` : `Running` ;
- nœud : `Ready`.

### Appliquer les manifests

Depuis la racine du dépôt :

```powershell
kubectl apply -k .\k8s\
```

Vérifier les ressources et le démarrage d’Open WebUI :

```powershell
kubectl get pods -n kubeai
kubectl get service -n kubeai
kubectl get endpoints -n kubeai
kubectl rollout status deployment/open-webui -n kubeai --timeout=5m
```

Pour vérifier les manifests sans les appliquer au cluster, utiliser :

```powershell
kubectl apply --dry-run=client -k .\k8s\
```

Cette commande simule l’application côté client et ne crée ni ne modifie de ressource Kubernetes.

### Accéder à Open WebUI

Dans un terminal séparé, démarrer le port-forward et garder ce terminal ouvert :

```powershell
kubectl port-forward -n kubeai service/open-webui 8080:80
```

Dans un autre terminal ou dans le navigateur, tester l’accès :

```powershell
Invoke-WebRequest http://127.0.0.1:8080 -UseBasicParsing
```

Résultat attendu :

```text
HTTP 200
```

L’interface est accessible à :

```text
http://127.0.0.1:8080
```

### Arrêter le tunnel

Dans le terminal du port-forward, appuyer sur `Ctrl + C`.

## Explication des commandes de validation

| Commande | Rôle |
|---|---|
| `docker version` | Vérifie que Docker Desktop fonctionne. |
| `minikube status` | Vérifie l’état du cluster Minikube. |
| `kubectl config current-context` | Affiche le cluster Kubernetes utilisé. La valeur attendue est `minikube`. |
| `kubectl get nodes` | Affiche les nœuds Kubernetes. Le nœud `minikube` doit être `Ready`. |
| `kubectl apply --dry-run=client -k .\k8s\` | Vérifie les manifests sans les appliquer ni modifier le cluster. |
| `kubectl get pods -n kubeai` | Affiche les applications du projet dans le namespace `kubeai`. |
| `kubectl rollout status deployment/open-webui -n kubeai --timeout=5m` | Vérifie que le déploiement Open WebUI est terminé et prêt. |
| `kubectl port-forward -n kubeai service/open-webui 8080:80` | Crée un tunnel entre le port local 8080 et Open WebUI dans Kubernetes. |

Après le `port-forward`, ouvrir :

http://127.0.0.1:8080

Le terminal qui exécute le `port-forward` doit rester ouvert. Pour arrêter le tunnel, utiliser `Ctrl+C`.