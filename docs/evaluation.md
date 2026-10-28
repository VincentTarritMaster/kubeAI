# Évaluation consolidée — KubeAI

**État au 28 octobre 2026.** Ce document consolide les preuves disponibles ; il
distingue les faits mesurés des objectifs qui restent à certifier.

## 1. Périmètre évalué

- deux clusters Minikube : `kubeai-observed` et `kubeai-supervision` ;
- collecte Alloy sur tous les namespaces du cluster observé ;
- Mimir pour les métriques, Loki pour les logs ;
- agent FastAPI avec tool agent LangChain Core ;
- Open WebUI sur `127.0.0.1:8080` ;
- fournisseur LLM : **Mistral API** (`mistral-small-2603`), pas de modèle local
  dans la configuration de démonstration.

## 2. Exactitude des réponses A/B/C

### A — santé du cluster

Une réponse du 27 octobre 2026 à 21:18 UTC indiquait :

- 11 pods ;
- 1/1 nœud prêt ;
- 60 redémarrages cumulés ;
- pod de démonstration `Succeeded` ;
- aucun pod malsain.

Les composants cités (`kube-state-metrics`, `kindnet`, `kube-proxy`,
`storage-provisioner`) correspondent aux objets réellement présents.

**Limite :** les réponses A en français et en anglais n'ont pas toujours la même
exhaustivité. Une réponse anglaise a signalé un échec de readiness
`kube-state-metrics` et des logs absents, alors que la réponse française
concluait « aucune donnée manquante ». Le socle factuel était proche, mais
l'équivalence sémantique FR/EN n'est pas garantie.

### B — chronologie de l'incident

Exécution réelle du 28 octobre 2026, fenêtre demandée
`08:00:09Z → 08:04:10Z` :

| Attendu | Observé dans la réponse |
|---|---|
| `phase=normal` | `08:00:09.821Z` |
| `phase=anomaly_start` | `08:01:39.809Z` |
| `connection refused` | `08:01:39.811Z` |
| `phase=recovered` | `08:03:09.908Z` |
| `phase=complete` | `08:04:09.901Z` |

**Résultat :** 5/5 marqueurs retrouvés, dans la bonne fenêtre.

### C — corrélation logs / CPU / HTTP

Même exécution, même fenêtre :

| Famille de preuve | Valeur observée |
|---|---|
| CPU conteneur | 0.019 → 0.088 c, limite 0.1 c |
| CPU application | 0.007 → 0.100 c |
| Mémoire working set | 4.34 → 15.90 Mo, limite 64 Mo |
| Ratio d'erreurs HTTP | 0.0 → 1.0 (100 %) |
| Latence HTTP p95 | 0.0475 → 0.195 s |
| Événements Kubernetes | aucun événement pertinent conservé |
| État final du pod | `Succeeded`, 0 redémarrage |

**Résultat :** la corrélation est cohérente avec les marqueurs de la démo et
les données Mimir/Loki. Les données absentes sont signalées, pas inventées.

### Ce que l'on peut conclure

Sur les scénarios contrôlés, les faits principaux de B/C sont exacts et les
données manquantes sont explicites.

On ne peut pas en déduire un taux général de **90 % de faits corrects** : il
n'existe pas de jeu de référence indépendant, de questions aléatoires ni de
double annotation. Le 90 % de la fiche reste donc **un objectif à mesurer**, pas
un résultat certifié.

## 3. Fiabilité

### Tests automatisés

| Suite | Résultat |
|---|---|
| Tests agent | **417 passed** |
| Tests scripts Python | **20 passed** |
| Tests shell/Bash | 11 non exécutables dans l'environnement Windows actuel (accès WSL/Bash refusé) |
| Compilation Python | OK |
| Parsing PowerShell | OK |

La validation macOS d'origine de la branche multi-cluster annonçait 31 tests de
scripts ; la différence correspond aux tests shell non rejoués sous Windows.

### Exécutions réelles

- démarrage des deux clusters ;
- collecte Alloy → Mimir/Loki ;
- démonstration complète avec marqueurs UTC ;
- B et C en conditions réelles ;
- nettoyage de la démo sans suppression des PVC.

### Incidents de fiabilité rencontrés et corrigés

- API Kubernetes non prête après redémarrage de Minikube → attente `/readyz` ;
- `--patch-file=/dev/stdin` non portable sous Windows → fichier temporaire ;
- `py -3` transformé en chaîne et lancé en REPL → tableau d'arguments corrigé ;
- tunnel WebUI orphelin sur le port 8080 → processus à arrêter avant relance ;
- réponse Mistral d'appel d'outil incomplète → retry, message explicite et
  synthèse de secours après le budget d'outils ;
- fenêtre UTC élargie par le modèle → garde-fou sur les bornes explicites.

## 4. Latence

La fiche vise une réponse en moins de 60 secondes.

**Constat :** la latence n'est pas instrumentée de bout en bout dans les
réponses. Le budget configuré est de **180 secondes** pour l'agent et les appels
Mistral peuvent prendre plusieurs dizaines de secondes. Les observations
manuelles montrent des réponses B/C généralement sous deux minutes, mais pas
systématiquement sous 60 secondes.

**Conclusion :** l'objectif `< 60 s` n'est **pas certifié**. Il faudrait mesurer
`temps de début de requête → dernier token` dans les logs et sur un échantillon
de questions.

## 5. Sécurité

### Mesures présentes

- agent en lecture seule : nœuds, pods, événements ;
- aucun Secret, `exec`, suppression ou modification exposé à l'agent ;
- Alloy : ClusterRole `pods`/`pods/log` pour tous les namespaces et
  `nodes/proxy` en lecture seule pour cAdvisor ;
- kubeconfig observé avec CA et jeton de durée limitée ;
- authentification Bearer entre Open WebUI et l'agent ;
- allowlist d'outils, validation Pydantic des arguments ;
- noms de pods vérifiés avant lecture ;
- fenêtres historiques bornées à six heures ;
- redaction des secrets détectables ;
- aucun shell, URL libre, PromQL/LogQL arbitraire ou remédiation ;
- garde-fou numérique sur les affirmations de la réponse finale.

### Limites de sécurité

- clé Bearer partagée, sans identité utilisateur forte ;
- **aucun journal d'audit structuré** ;
- **aucun quota de requêtes** ;
- deux clusters locaux et un réseau Docker privé, sans Ingress/TLS public ;
- pas de NetworkPolicies cloud ;
- les logs et résultats d'outils sont traités comme des données non fiables,
  mais la synthèse finale reste produite par un LLM.

## 6. Limites connues

- cohérence FR/EN non garantie pour A ; B/C sont validés en français, l'anglais
  repose sur les tests et les instructions de langue ;
- événements Kubernetes parfois expirés ou tronqués ;
- métriques HTTP détaillées (4xx/5xx) non toujours disponibles, seul le ratio
  global peut l'être ;
- Mistral peut renvoyer un appel d'outil mal formé ; un retry et une synthèse de
  secours sont prévus ;
- budget d'outils limité ; après plusieurs lectures, la synthèse se fait sur les
  preuves déjà collectées ;
- démonstration locale sur 8 Go : Minikube est lent et les deux clusters
  consomment 4 Gio + 2 Gio ;
- données de démonstration synthétiques, pas un trafic de production ;
- aucune évaluation statistique sur un jeu de questions indépendant.

## 7. Conclusion

**Points solides :**

- architecture deux clusters réelle ;
- tous les namespaces collectés ;
- RBAC read-only ;
- A/B/C reproductibles ;
- B/C factuels sur les scénarios contrôlés ;
- 417 tests agent.

**Points non certifiés :**

- 90 % de faits corrects sur un jeu représentatif ;
- réponse systématiquement sous 60 secondes ;
- équivalence stricte FR/EN ;
- audit et quotas.

Ces points doivent être présentés comme des limites ou des travaux restants,
pas comme des résultats acquis.
