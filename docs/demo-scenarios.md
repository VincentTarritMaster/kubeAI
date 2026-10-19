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

Relier logs et métriques pour proposer des hypothèses de cause, en distinguant clairement les faits observés des hypothèses.
