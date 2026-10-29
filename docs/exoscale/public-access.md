# Accès public KubeAI — `https://kubeai.anassndri.ch`

## Solution retenue

**Caddy sur le nœud de supervision, avec `hostPort` 80/443 et certificat
Let's Encrypt automatique.**

- Aucun Load Balancer payant.
- Aucun nouveau volume : Caddy réutilise le PVC `open-webui-data`, sous-dossier
  `caddy`, pour conserver ses certificats.
- Seul Open WebUI est proxyfié. Mimir, Loki, l'API agent, l'API Kubernetes et
  les NodePorts d'observabilité restent internes.
- Caddy écoute uniquement sur le nœud public `pool-e7b11-rtnnh`.

## Coût

| Élément | Coût |
|---|---|
| Caddy, Let's Encrypt | 0 CHF |
| Règles Security Group 80/443 | 0 CHF |
| PVC supplémentaire | aucun (PVC existant réutilisé) |
| Exoscale NLB (non retenu) | 25 CHF/mois fixes hors taxes |

Source NLB : <https://www.exoscale.com/blog/network-load-balancer-release/>

Aucun service payant n'a été ajouté.

## DNS exact à saisir chez Infomaniak

Dans **Domaines → anassndri.ch → Zone DNS**, ajouter :

| Champ | Valeur exacte |
|---|---|
| Type | `A` |
| Nom / sous-domaine | `kubeai` |
| Cible / valeur | `151.145.199.74` |
| TTL | `300` secondes |

Ne pas créer de `CNAME`, ne pas créer de `AAAA`, ne pas modifier les serveurs
DNS principaux.

`151.145.199.74` est l'adresse IPv4 publique actuelle de l'instance Exoscale
`pool-e7b11-rtnnh` (cluster `kubeai-supervision`). Elle est vérifiable avec :

```powershell
exo -A kubeai_windows_cli compute instance list --zone ch-gva-2 -O json
```

Cette adresse n'est pas garantie si l'instance est recréée. Pour une adresse
stable et une haute disponibilité, la seule option Exoscale est le NLB, payant
et non autorisé ici.

## Security Group — action manuelle obligatoire

Le rôle IAM `kubeai-deployer` refuse la modification des Security Groups
(`Forbidden by role policy for compute`). Les règles suivantes doivent être
ajoutées manuellement dans **Compute → Security Groups → kubeai-private-sg →
Ingress** :

| Protocole | Port | Source | Description |
|---|---|---|---|
| TCP | 80 | `0.0.0.0/0` | `kubeai public HTTP ACME` |
| TCP | 443 | `0.0.0.0/0` | `kubeai public HTTPS` |

Ne pas ouvrir 30009, 30310, 10250 ni aucun autre port. Les règles privées
existantes doivent rester intactes.

Sans ces règles, `https://kubeai.anassndri.ch` ne peut pas être atteint depuis
Internet et Let's Encrypt ne peut pas valider le domaine.

## Ordre des opérations

1. Ajouter l'enregistrement DNS `A kubeai → 151.145.199.74`, TTL 300.
2. Ajouter les deux règles Security Group 80/443.
3. Attendre la propagation DNS :

   ```powershell
   Resolve-DnsName kubeai.anassndri.ch -Server 1.1.1.1
   ```

4. Attendre que Caddy obtienne le certificat. Caddy réessaie automatiquement.
   En cas de rate-limit Let's Encrypt après des essais NXDOMAIN, attendre la
   fenêtre indiquée dans les logs Caddy.
5. Vérifier depuis un réseau extérieur :

   ```powershell
   curl.exe -I https://kubeai.anassndri.ch
   curl.exe -sS https://kubeai.anassndri.ch/api/config
   ```

6. Se connecter avec le compte local. Le mot de passe de démonstration a été
   remplacé ; les identifiants sont hors Git dans
   `%USERPROFILE%\.kube\kubeai-webui-credentials.json`.

## Validation smartphone 5G

1. Désactiver le Wi-Fi du téléphone.
2. Ouvrir `https://kubeai.anassndri.ch`.
3. Vérifier le cadenas TLS et le nom du certificat.
4. Se connecter.
5. Sélectionner le modèle `kubeai`.
6. Poser la question A, puis B/C si nécessaire.
7. Vérifier que la réponse arrive en streaming (texte progressif).
8. Ne pas utiliser `http://` ni `https://…:port`.

## Sécurité

- Open WebUI : authentification active, inscriptions publiques désactivées.
- Mot de passe de démonstration précédemment exposé remplacé.
- Caddy : administration locale désactivée, HSTS, en-têtes de sécurité.
- Seuls 80/443 sont exposés ; l'observabilité reste privée.
- Rate limit agent : 10 requêtes/minute, burst 5, pour limiter les abus Mistral.
- Aucun secret dans Git ni dans ce document.

## État actuel vérifié

- Caddy : `1/1 Running`.
- Open WebUI : `1/1 Running`, backend accessible depuis Caddy.
- DNS : `kubeai.anassndri.ch → 151.145.199.74`.
- Security Group public 80/443 : ouvert.
- Certificat public : Let's Encrypt production, `CN=kubeai.anassndri.ch`,
  émis par `YE2`, vérification OpenSSL `0 (ok)`.
- `https://kubeai.anassndri.ch` : **HTTP 200**, `Server: uvicorn` via Caddy.
- `auth=true`, `enable_signup=false`.
- Connexion et streaming testés depuis Internet : A/B/C HTTP 200.
- 30009, 30310 et 10250 restent fermés publiquement.

Le certificat est stocké dans le PVC `open-webui-data`, sous-dossier `caddy`.
Caddy le renouvelle automatiquement.
