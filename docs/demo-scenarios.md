# Scénarios de démonstration

Les trois démonstrations prévues reprennent le sujet du projet. Le premier test de bout en bout ci-dessous a été exécuté ; les scénarios plus avancés restent à préciser.

## Incident visible dans Open WebUI

Test exécuté le 18 octobre 2026 sur le contexte local `minikube`, dans le namespace `kubeai`, avec Apertus 8B.

1. Lancer `./start-failure.sh` : il crée `kubeai-demo-failure` avec `restartPolicy: Never`, affiche des messages identifiables, puis termine avec le code `42`. Le message de variable absente est une simulation : le script affiche du texte, sans vérifier réellement une dépendance. Un message supplémentaire explicite cette limite.
2. Vérifier l'état `Failed` et le code de sortie dans Kubernetes. Comparer `kubectl logs --timestamps` aux lignes collectées par Loki pour ce pod.
3. Dans une nouvelle conversation Open WebUI avec le modèle `kubeai-apertus`, demander : « Quels pods ont un problème dans kubeai ? Identifie le pod concerné, cite son état Kubernetes et les logs pertinents, et distingue faits et hypothèses. » Poser la question dans les dix minutes suivant l'incident, fenêtre de logs utilisée par l'agent.
4. Vérifier le nom du pod, son état et les citations des logs. Les hypothèses doivent rester distinctes des faits ; le texte simulé ne prouve pas une panne réelle de base de données.
5. Lancer `./stop-failure.sh` pour supprimer uniquement ce pod temporaire, puis vérifier que `/cluster/status` revient à `healthy` et que les services restent prêts.

Résultat : le pod `kubeai-demo-failure-67cd36f8` a terminé en `Failed` avec le code `42`. Ses deux lignes étaient identiques dans Kubernetes et Loki. La réponse Open WebUI, transmise par l'utilisateur car l'accès navigateur de l'outil était bloqué, identifiait le bon pod et citait exactement les deux lignes. Après suppression, l'agent indiquait deux pods prêts, aucun pod en erreur et un état `healthy`.

Réserves observées lors de ce premier test : la réponse suggérait une détection effective de variable absente alors que le pod ne faisait qu'afficher un message simulé ; elle contenait également deux horodatages de relevé différents.

Corrections : les consignes imposent de signaler l'incident simulé sans présenter la variable absente comme un fait établi. L'horodatage automatique des réponses a été supprimé ; seules les sources sont ajoutées. Les anciens pieds de page sont retirés de l'historique transmis au modèle et les relevés générés par le modèle sont filtrés, y compris en streaming. Les horodatages des événements utiles au diagnostic restent disponibles.

Vérification des corrections : 25 tests unitaires passent. Avec Apertus local et le nouveau pod de démonstration, les réponses normales et en streaming distinguent explicitement la simulation d'une panne réelle, ne reprennent pas l'ancien horodatage injecté dans l'historique et contiennent une seule liste des sources, sans date de relevé. Ces vérifications ont utilisé l'API de l'agent. Les scripts ont été exécutés avec création depuis le manifeste séparé, démarrage répété, suppression et arrêt répété sans erreur. Les réponses de contrôle sont conservées localement dans `.cache/incident-tests/fixes-live.json`.

Les preuves locales de cette exécution (état Kubernetes, logs, réponse copiée et comparaison) sont conservées dans `.cache/incident-tests/67cd36f8/`, dossier ignoré par Git.

## Santé globale du cluster

**Question exacte dans Open WebUI, modèle `kubeai` :** « Quel est l'état de santé de mon cluster ? »

Cette question emprunte le chat existant (`/v1/chat/completions`, JSON ou SSE) et
`/diagnose`. Elle déclenche un relevé global, distinct du bilan de namespace
`/cluster/status`. L'agent lit `/cluster/inventory` par son service interne :
nœuds, pods de tous les namespaces, états et redémarrages des conteneurs,
événements Kubernetes `Warning` datés des quinze dernières minutes. Il interroge
Mimir pour les sommes CPU et mémoire des conteneurs de pods observés dans tous
les namespaces. Ces mesures ne sont ni la capacité des nœuds ni un taux de
saturation. Aucune source d'alertes dédiée n'est configurée : l'agent n'en invente pas.
Les messages bruts des événements ne sont pas transmis à Mistral ; seuls motif,
objet concerné et horodatage sont retenus dans le résumé borné.

### Règles déterministes du projet

Ces règles sont des choix du groupe pour la démonstration, **pas des seuils
imposés par le professeur** :

- **Critique** : tous les nœuds observés portent explicitement `NotReady` et
  aucun n'est `Ready`.
- **Dégradé** : au moins un nœud `NotReady`, un pod `unhealthy`, un conteneur
  avec au moins trois redémarrages **cumulés**, ou un événement `Warning` daté
  de moins de quinze minutes. Une dégradation observée reste signalée même si
  une autre source est partielle.
- **Indéterminé** : aucun signal certain de dégradation, mais nœuds ou pods
  indisponibles, partiels, inconnus, ou aucun nœud observé.
- **Sain selon les contrôles effectués** : nœuds et pods lisibles, nœuds Ready,
  aucun pod malsain, aucun cumul de redémarrages atteignant le seuil et aucun
  `Warning` récent dans les événements disponibles. Cela ne prouve pas la santé
  fonctionnelle de chaque application.

Mimir fournit des **valeurs** ; aucune qualification « CPU élevé » ou « mémoire
saturée » n'est faite sans capacité ni seuil validé. Une valeur absente reste
absente. Les événements de plus de quinze minutes ne déclenchent pas l'état
dégradé. Les contradictions entre nœud NotReady et pod Ready sont indiquées en
limite du relevé. Les inventaires restent bornés comme indiqué dans le contrat API.

Le diagnostic, les chiffres, les preuves, les limites et les vérifications en
lecture seule sont produits avant tout appel au modèle. Si Mistral est disponible,
il ne choisit qu'une priorité parmi des catégories appuyées par les preuves ;
son texte libre n'est pas publié. Sans clé ou en cas d'échec, le bilan déterministe
reste disponible avec la mention que l'analyse IA avancée est indisponible.

### Scénarios à vérifier

| Situation simulée | Résultat attendu et vérifiable |
|---|---|
| Deux nœuds Ready, pods Running dans plusieurs namespaces, aucun Warning récent | « Sain selon les contrôles effectués » ; nombres et valeurs Mimir issus des réponses simulées |
| Un nœud NotReady, pods Pending/Failed/CrashLoopBackOff ou redémarrages cumulés ≥ 3 | « Dégradé » ; nœuds, pods et conteneurs concernés cités avec leur namespace |
| Tous les nœuds explicitement NotReady | « Critique » selon la règle locale, sans cause inventée |
| Accès Kubernetes refusé ou indisponible | « Indéterminé » si aucune dégradation certaine n'est observable ; listes absentes signalées |
| Mimir ou Mistral indisponible | Diagnostic Kubernetes maintenu ; mesures ou analyse avancée signalées absentes |

Les tests HTTP simulés couvrent ces scénarios, les événements récents, les
données contradictoires, le filtrage d'une proposition non étayée de Mistral et
les formats JSON/SSE. Ils n'établissent pas que le déploiement fonctionne sur un
cluster réel.

### Vérification ultérieure sur un cluster autorisé

Sur une machine adaptée, après déploiement de la nouvelle image et des manifests,
utiliser un contexte Kubernetes explicite. Ces commandes sont en lecture seule ;
elles **n'ont pas été exécutées sur le poste Windows de 8 Go** :

```powershell
$ctx = 'nom-du-contexte'
kubectl --context $ctx get nodes
kubectl --context $ctx get pods -A
kubectl --context $ctx get events -A --sort-by=.metadata.creationTimestamp
kubectl --context $ctx -n kubeai port-forward service/kubeai-agent 8000:8000
```

Dans un second terminal PowerShell, comparer
`Invoke-RestMethod 'http://127.0.0.1:8000/cluster/inventory'` et la réponse
obtenue dans Open WebUI avec les trois relevés Kubernetes. Contrôler les valeurs
Mimir séparément, sans conclure à une saturation à partir du seul usage.

## Investigation avec chronologie

La démonstration B utilise le pod volontairement échoué
`k8s/demo/failure-pod.yaml`. Ses messages parlent d'une variable de
démonstration et d'un code de sortie `42` ; ils ne prouvent aucune panne de base
de données ou de service externe. Les tests automatisés emploient uniquement
des réponses HTTP simulées. Le protocole suivant reste **à effectuer** sur un
cluster autorisé ; il n'a pas été lancé sur le poste Windows de 8 Go.

### Préparation et déclenchement ultérieur

Choisir explicitement un contexte et vérifier que le namespace `kubeai`, l'agent,
Mimir et Loki sont accessibles. Noter l'heure avant et après la simulation, avec
son décalage UTC ; la fenêtre doit couvrir l'événement et rester sous six heures.

```powershell
$ctx = 'nom-du-contexte'
kubectl --context $ctx -n kubeai get pods
$debut = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
kubectl --context $ctx apply -f k8s/demo/failure-pod.yaml
kubectl --context $ctx -n kubeai get pod kubeai-demo-failure -w
$fin = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
```

Arrêter `-w` lorsque le pod est `Failed`. Dans Open WebUI, sélectionner le modèle
`kubeai` puis poser la question en remplaçant les deux dates par les valeurs
relevées :

```text
Que s'est-il passé lors de l'incident début=2026-10-19T14:00:00+02:00 fin=2026-10-19T14:30:00+02:00 namespace=kubeai pod=kubeai-demo-failure ?
```

Une question « incident de 14 h » sans date, borne de fin ni fuseau reçoit une
demande de précision, sans collecte historique. Les bornes ISO explicites
déclenchent la lecture bornée des événements Kubernetes, des logs Loki et des
échantillons CPU/mémoire historiques Mimir. La chronologie attendue classe les
observations réellement présentes par heure UTC et cite leur source et leur
référence. Le log de sortie `42` peut figurer s'il a été collecté ; un événement
`Warning` peut figurer s'il existe encore ; des mesures peuvent manquer pour un
pod très court. Le système ne fabrique pas les observations absentes. L'état
`Failed` et le cumul des redémarrages lus ensuite sont présentés comme état
actuel, séparé des faits historiques.

Pour confronter la réponse aux sources, utiliser en lecture seule :

```powershell
kubectl --context $ctx -n kubeai get pod kubeai-demo-failure -o yaml
kubectl --context $ctx -n kubeai logs kubeai-demo-failure --timestamps
kubectl --context $ctx -n kubeai get events --sort-by=.metadata.creationTimestamp
```

Comparer les horodatages aux données Loki et Mimir pour la même fenêtre UTC.
Un événement absent peut avoir expiré ; une mesure absente ne signifie pas zéro.
Les horloges des sources peuvent diverger. Une proximité dans la chronologie
n'établit pas la cause racine. Vérifier que la réponse distingue bien constats,
hypothèses, données manquantes et vérifications en lecture seule.

Après le contrôle, supprimer uniquement le pod de démonstration :

```powershell
kubectl --context $ctx delete -f k8s/demo/failure-pod.yaml --ignore-not-found
kubectl --context $ctx -n kubeai get pod kubeai-demo-failure
```

Résultat observable des tests **simulés** : événements remis dans l'ordre,
log et mesures associés à leurs horodatages, absence de fausse causalité,
réponse JSON/SSE cohérente et repli déterministe sans Mistral. La rétention
réelle des événements, la collecte de logs, la présence d'échantillons Mimir et
le rendu Open WebUI restent à valider sur cluster.

## Corrélation et hypothèses de cause

La démonstration C réutilise la collecte historique de B. Dans la configuration
active, Alloy écrit dans Mimir les métriques CPU et mémoire cAdvisor et scrute
kube-state-metrics. Le backend interroge aussi les séries historiques de
redémarrages et de limites CPU/mémoire ; leur absence reste explicite. Aucun
compteur d'erreurs HTTP ni histogramme de latence NestJS n'est instrumenté dans
ce dépôt. Une question sur une lenteur NestJS ne peut donc pas promettre une
cause liée au temps de réponse : il faut ajouter une instrumentation HTTP dans
l'application, puis sa cible de scrape Alloy, et confirmer les noms et labels
des séries Mimir avant d'activer la règle CPU + latence.

### Règles de corrélation déterministes

Ces seuils sont des heuristiques locales de démonstration, **pas des seuils du
professeur ni des scores de confiance numériques**. Les observations doivent
concerner le même pod et être éloignées de cinq minutes au plus. Une règle
appuyée reste une hypothèse : la proximité temporelle ne démontre pas la cause.

| Hypothèse | Preuves exigées | Réserve |
|---|---|---|
| Pression mémoire compatible avec OOM | Événement `OOMKilled`, augmentation d'une série historique de redémarrages autour de l'événement, working set ≥ 90 % d'une limite mémoire historique réellement mesurée | Un pic bref ou un autre conteneur peut expliquer l'écart ; une mémoire < 50 % de la limite est signalée comme contradiction |
| Connectivité possible | Log de connexion refusée ou timeout ; un événement réseau proche renforce l'hypothèse | Un log isolé donne « preuves insuffisantes » ; le service distant ou l'application peuvent être en cause |
| Contention CPU possible | CPU ≥ 80 % d'une limite historique et latence HTTP p95 réellement instrumentée ≥ 0,2 s et au moins doublée par rapport à un échantillon antérieur | La latence peut venir d'une dépendance ; **la règle n'est pas activable sur la configuration NestJS actuelle faute de métrique p95** |
| Activité CPU du scénario | Marqueur `KUBEAI_DEMO_CPU_LOAD`, puis CPU accru d'au moins 0,05 cœur et doublé par rapport à un échantillon antérieur | Corrélation limitée ; le marqueur n'est pas une mesure de latence |

Chaque hypothèse expose identifiant, description en français, références des
observations, ressource, intervalle UTC, confiance qualitative, alternatives,
contradictions et vérifications en lecture seule. Les métriques absentes,
événements expirés, collectes tronquées et sources en échec conduisent à un
statut partiel ou à « cause indéterminée ». Mistral peut seulement choisir la
priorité parmi les hypothèses calculées ; son texte libre n'est pas publié.

### Scénario simulé et scénario déployable

Les tests unitaires utilisent des réponses Mimir, Loki et Kubernetes simulées.
Ils couvrent les hypothèses OOM, connexion, CPU + latence **avec p95 injectée
dans le test**, contradictions et données manquantes. Cela ne prouve pas la
présence d'une série HTTP dans un cluster réel.

Le manifeste [correlation-pod.yaml](../k8s/demo/correlation-pod.yaml) est un
petit pod BusyBox distinct du déploiement normal. Il attend 35 secondes,
émet un marqueur `KUBEAI_DEMO_CPU_LOAD`, lance une activité CPU pendant
90 secondes, puis s'arrête. Le CPU cAdvisor devrait monter par rapport à
la période d'attente, et Alloy devrait collecter les marqueurs Loki si les
composants sont opérationnels. C'est une charge **artificielle**, sans trafic
HTTP et sans panne de production. Le manifeste limite CPU et mémoire, coupe
le jeton de ServiceAccount et n'ajoute aucune permission RBAC.

Le protocole suivant est **déployable mais non exécuté** sur le poste Windows
de 8 Go. Sur un cluster autorisé, relever les bornes avec fuseau, puis comparer
les preuves aux sources avant de conclure :

```powershell
$ctx = 'nom-du-contexte'
kubectl --context $ctx -n kubeai get pods
$debut = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
kubectl --context $ctx apply -f k8s/demo/correlation-pod.yaml
kubectl --context $ctx -n kubeai get pod kubeai-demo-correlation -w
$fin = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
kubectl --context $ctx -n kubeai logs kubeai-demo-correlation --timestamps
kubectl --context $ctx -n kubeai get events --sort-by=.metadata.creationTimestamp
```

Interrompre la veille `-w` après la fin du pod. Dans Open WebUI, modèle `kubeai`,
poser la question en remplaçant les dates par `$debut` et `$fin` :

```text
Pourquoi le pod kubeai-demo-correlation est-il lent ? Corrèle les signaux début=2026-10-19T14:00:00+02:00 fin=2026-10-19T14:30:00+02:00 namespace=kubeai pod=kubeai-demo-correlation
```

La réponse attendue est limitée à l'activité CPU et au marqueur si tous deux
sont collectés. Elle ne doit pas prétendre mesurer la latence de NestJS ni
attribuer une panne externe. Vérifier les valeurs Mimir sur la même fenêtre :

```powershell
kubectl --context $ctx -n monitoring port-forward service/mimir 9009:9009
```

Dans un second terminal, interroger `/prometheus/api/v1/query_range` avec
`rate(container_cpu_usage_seconds_total{namespace="kubeai",pod="kubeai-demo-correlation"}[2m])`,
les bornes UTC et un pas de 30 secondes. Comparer les lignes Loki à `kubectl logs
--timestamps`. Les événements Kubernetes peuvent avoir expiré ; la pagination
de l'inventaire est plafonnée et une collecte partielle ne prouve pas une cause.

Nettoyer uniquement ce pod de démonstration :

```powershell
kubectl --context $ctx delete -f k8s/demo/correlation-pod.yaml --ignore-not-found
kubectl --context $ctx -n kubeai get pod kubeai-demo-correlation
```

Résultat **effectivement testé sur Kubernetes pour C : aucun à ce stade**.
