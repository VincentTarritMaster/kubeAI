# LangChain et appels de fonctions

Le chat courant utilise **LangChain Core** pour décrire des fonctions de lecture
avec `StructuredTool` et des schémas Pydantic, puis les convertir au format
du fournisseur via `convert_to_openai_tool`.

Le modèle choisit les fonctions et leurs arguments à partir de la conversation.
Le contrôleur FastAPI valide les appels, exécute les lectures Kubernetes,
Mimir et Loki, puis renvoie les résultats au modèle. Celui-ci peut demander
d'autres preuves avant de terminer avec `respond`.

Le [fonctionnement complet de l'agent](agent-tools.md) décrit les outils,
les langues, le contexte et les limites d'exécution.

## Code

- `agent/app/services/observability_tools.py` : registre des outils et schémas.
- `agent/app/services/tool_agent.py` : boucle de décision et d'exécution.
- `agent/app/services/llm_service.py` : transport HTTP et validation des réponses natives.
- `agent/app/prompts/tool_agent.txt` : instructions du modèle.

Le profil local utilise une décision JSON contrainte (`name`, `arguments`) ;
Mistral utilise les appels natifs. Les deux formats passent par le même registre
et le même contrôleur. Le transport reste `httpx` pour conserver l'authentification fournisseur, les
timeouts et les erreurs sanitisées. Le contrôleur utilise ses propres budgets
et validations. Aucun `AgentExecutor` n'est requis pour cette boucle.

## Compatibilité

Le mode explicite `KUBEAI_CHAT_MODE=legacy` conserve la précédente chaîne
`ChatPromptTemplate` → appel borné → sélection dans une allowlist, avec des
réponses déterministes. Ses anciens tests restent exécutés. Le processus et
le déploiement utilisent par défaut `agent`, où le modèle choisit les outils
et rédige la réponse.
