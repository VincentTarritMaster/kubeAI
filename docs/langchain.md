# Chaîne LangChain

KubeAI utilise **FastAPI** pour l’API et **LangChain Core 1.7** pour la chaîne de
sélection/priorisation du LLM. Ce n’est pas un agent LangChain autonome : les
outils Kubernetes, Mimir et Loki restent appelés par le backend, avec des bornes
et des allowlists.

## Pipeline

```text
ChatPromptTemplate
  system : instructions serveur + schéma JSON
  user   : observations déjà bornées et redactées
        |
        v
RunnableLambda : appel LLM borné
  - Mistral : response_format json_schema strict
  - local   : paramètres chat ordinaires
        |
        v
RunnableLambda : validation de la sortie
  - JSON uniquement
  - clé et valeur dans l’allowlist
  - rejet du texte libre, des clés dupliquées et des réponses tronquées
        |
        v
une valeur d’énumération, jamais de prose
```

Le code est dans
[`agent/app/services/llm_service.py`](../agent/app/services/llm_service.py).

## Pourquoi le transport HTTP reste direct

LangChain orchestre le prompt, l’appel et la validation. Le transport HTTP reste
`httpx` pour :

- conserver le streaming SSE d’Open WebUI ;
- envoyer le `response_format` JSON Schema attendu par Mistral ;
- garder les erreurs fournisseur sanitisées et les timeouts existants ;
- éviter un agent qui exécuterait des outils non bornés.

C’est une chaîne LangChain, pas un `AgentExecutor` avec des tools dynamiques.

## Sécurité

- Le prompt système et l’allowlist viennent du code serveur.
- Les observations sont redactées avant l’appel.
- Le LLM ne peut retourner qu’une valeur d’énumération déjà calculée.
- Une sortie invalide lève `UpstreamError` et le diagnostic déterministe reste
  disponible.
- Aucune remédiation, aucun `exec`, aucun accès Secrets.

## Vérification

`/capabilities` expose :

```json
"llm": {
  "provider": "mistral-api",
  "model": "mistral-small-2603",
  "framework": "langchain",
  "configured": true
}
```

Les tests de sélection couvrent le schéma strict, le JSON local, les fences,
les clés dupliquées, les valeurs hors allowlist, les réponses tronquées et les
erreurs HTTP sanitisées. La suite complète compte 360 tests + 6 tests loopback.

## Limite à assumer

Le sujet parle d’« orchestration des outils ». Ici :

- les outils sont choisis par le routeur FastAPI, pas par le LLM ;
- LangChain apporte la chaîne de prompt et la validation structurée ;
- le LLM ne fait que prioriser une hypothèse.

C’est un choix de fiabilité et de sécurité, à expliquer dans le rapport.
