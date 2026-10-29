# KubeAI Exoscale — preuves de déploiement

Date : 29 octobre 2026. Zone : `ch-gva-2`.

## Accès Open WebUI

Depuis le poste local :

```powershell
$env:KUBECONFIG = "$HOME\.kube\kubeai-exoscale\kubeai-observed.yaml;$HOME\.kube\kubeai-exoscale\kubeai-supervision.yaml"
kubectl --context kubeai-supervision -n kubeai port-forward svc/open-webui 18080:80
```

Ouvrir <http://127.0.0.1:18080>.

Le compte local est décrit dans
`%USERPROFILE%\.kube\kubeai-webui-credentials.json` (hors dépôt). Ne pas
publier ce fichier.

## Démonstration A — santé

Question française :

> Quel est l'état de santé de mon cluster ?

Question anglaise :

> What is the current health status of my Kubernetes cluster? Check nodes, pods, restarts and recent alerts. Answer in English.

Réponses :

- [`demo-a-fr.json`](demo-a-fr.json)
- [`demo-a-en.json`](demo-a-en.json)

Le diagnostic est fondé sur Kubernetes, Mimir et les événements réels. Le
cluster observé est signalé **dégradé** à cause de réplicas système CoreDNS /
konnectivity en attente sur un cluster à un seul nœud, pas d'une panne des
composants KubeAI.

## Démonstration B — chronologie

> Analyse l'incident du pod kubeai-demo-incident dans le namespace applications, le 29 octobre 2026 entre 09:08 et 09:13 UTC. Reconstitue la chronologie à partir des logs et métriques réellement disponibles.

Réponse : [`demo-b-fr.json`](demo-b-fr.json)

Marqueurs réels trouvés dans Loki :

```text
09:08:53 phase=normal
09:10:23 phase=anomaly_start
09:10:23 KUBEAI_DEMO_DEPENDENCY connection refused
09:11:53 phase=recovered
09:12:53 phase=complete
```

## Démonstration C — corrélation

> Pour l'incident du pod kubeai-demo-incident dans le namespace applications, le 29 octobre 2026 entre 09:08 et 09:13 UTC, corrèle les logs, le CPU et les erreurs HTTP. Propose des hypothèses et les preuves disponibles.

Réponse : [`demo-c-fr.json`](demo-c-fr.json)

La réponse contient les métriques réelles CPU/HTTP, les logs et les hypothèses
limitées, avec les incertitudes.

## Preuves d'infrastructure

- [`cluster-state.txt`](cluster-state.txt) : nœuds, pods, PVC.
- [`data-sources.txt`](data-sources.txt) : requête Mimir et labels Loki.
- [`webui-login.png`](webui-login.png) : capture de la page de connexion.

## Vérifications de sécurité

- proxy mTLS sans certificat client : HTTP 400 ;
- Alloy avec certificat client : envois Mimir `200` et Loki `204` dans les logs
  du proxy ;
- Mimir et Loki accessibles uniquement par `ClusterIP`/proxy NodePort privé ;
- aucun Load Balancer public.

## Limites

- Les captures d'écran des conversations A/B/C doivent être prises depuis le
  navigateur local avec le compte WebUI ; elles ne sont pas automatisées ici.
- Les réplicas CoreDNS/konnectivity en attente expliquent le statut « dégradé »
  de la démonstration A sur un cluster à un seul nœud.

Aucun secret, aucune clé API et aucun certificat privé ne figurent dans ce
dossier.
