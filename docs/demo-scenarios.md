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

Présenter dans l’ordre les signaux pertinents d’un incident concernant un nœud, un pod ou un service.

## Corrélation et hypothèses de cause

Relier logs et métriques pour proposer des hypothèses de cause, en distinguant clairement les faits observés des hypothèses.
