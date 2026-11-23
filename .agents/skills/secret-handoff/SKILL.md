---
name: secret-handoff
description: "Configurer ou recevoir une clé API, clef, jeton, token, mot de passe de service ou secret CI sans l'exposer. Utiliser lorsqu'un agent a besoin d'un accès externe, quand l'utilisateur demande où enregistrer une clé ou quand le collage masqué échoue sous Windows. Ne pas utiliser pour une simple explication sans configuration d'accès."
---

# Clés et jetons

Préparer une configuration exploitable sans demander à l'utilisateur de transmettre
la valeur du secret dans le chat. Réutiliser les accès déjà configurés et respecter
les autorisations existantes ; ce skill n'ajoute pas une confirmation obligatoire
aux actions déjà autorisées.

## Choisir la destination avant de demander la clé

1. Identifier le service, l'opération et le lieu d'exécution : outil connecté,
   ordinateur local ou workflow hébergé. Déduire ces éléments du contexte.
2. Consulter les outils connectés disponibles avant de demander un nouvel accès.
   Si un coffre fournit déjà le secret au processus approprié, utiliser cette
   injection sans tenter d'en lire ou d'en extraire la valeur.
3. Pour une exécution GitHub Actions, demander un **repository secret** dans
   `Settings → Secrets and variables → Actions → Secrets → New repository secret`.
   Fournir le lien du dépôt concerné, le **Name** exact et l'origine de la valeur.
   Le champ **Secret** reçoit la valeur. Les variables ordinaires sont publiques
   aux collaborateurs autorisés à les consulter et ne conviennent pas aux clés.
4. Pour un usage local, préférer le coffre de secrets ou l'accès connecté existant.
   Sinon, préparer une saisie locale adaptée à l'outil avant de demander à
   l'utilisateur de créer la clé. Ne conserver le secret que si cette persistance
   est nécessaire et autorisée, dans un emplacement protégé hors Git.
5. Proposer des permissions limitées au service et au projet nécessaires. Donner
   des instructions directement réalisables ; ne pas demander plusieurs fois
   la même autorisation ni créer un nouvel accès sans nécessité.

## Procédure à donner à l'utilisateur

Indiquer, en quelques étapes :

- Le service officiel dans lequel créer ou retrouver la clé.
- La destination exacte et le nom du secret attendu.
- Le moyen de saisie : interface du coffre ou mécanisme local sans affichage.
- Comment signaler que la configuration est faite, sans envoyer sa valeur.

Un identifiant de projet, un identifiant de conversation et une clé d'authentification
remplissent des fonctions différentes. Expliquer cette différence quand elle est
pertinente, sans traiter automatiquement tous les identifiants comme des clés.

Ne pas placer une clé dans un message, un ticket, un commit, une capture d'écran,
un argument de commande, un fichier de suivi ou un exemple réaliste. Ne pas
demander de capture du contenu d'un champ Secret.

## Saisie sous Windows

Éviter le collage dans une saisie masquée de terminal comme parcours par défaut :
certains terminaux transmettent le caractère de contrôle `Ctrl+V` au programme.

Préférer une interface de secret adaptée ou un assistant local qui :

1. Démarre avant la copie du jeton, pour que copier la commande de lancement
   n'écrase pas le jeton avant sa lecture.
2. Demande de copier uniquement la clé depuis son fournisseur, puis d'appuyer sur
   Entrée sans la coller dans le terminal.
3. Lit le presse-papiers seulement à cette étape, sans l'afficher ni l'enregistrer.
4. Valide le format attendu pour ce fournisseur avant tout appel réseau.
5. Présente les erreurs sans reproduire la clé, l'URL authentifiée ni les sorties
   brutes d'un sous-processus.
6. N'insère pas la clé dans les arguments d'un sous-processus.

Ne pas lire le presse-papiers de l'utilisateur pour inspecter son contenu.
Laisser l'assistant local appelé par l'utilisateur consommer la valeur pour le
service prévu. Tester le code avec de fausses valeurs et des accès simulés.

Une erreur contenant `/bot\\x16/getUpdates` survient avant la requête Telegram :
attendre ou renvoyer `/start` ne corrige pas ce problème de saisie.

## Vérifier sans dévoiler

Distinguer trois états dans les notes durables et dans les réponses :

- **Déclaré par l'utilisateur** : il dit avoir ajouté le secret.
- **Présence vérifiée** : une capacité autorisée confirme que le nom existe.
- **Accès vérifié** : une opération réelle, précisément décrite, réussit.

Ne pas annoncer l'accès comme testé après un simple contrôle de format,
une simulation ou une déclaration de présence. Utiliser uniquement les outils
qui autorisent la vérification ; ne pas inventer un accès à l'API des secrets.

Pour un test réel autorisé, injecter le secret uniquement dans le processus qui en
a besoin. Choisir une opération limitée et pertinente. Un appel de lecture ne
prouve pas que l'écriture fonctionne. Un test d'authentification ne prouve pas
qu'un modèle peut générer une réponse ou qu'un workflow planifié peut livrer.

Conserver seulement le résultat, le nom du secret, sa destination et la date du
test. Réduire les réponses API à ce qui est nécessaire ; ne pas journaliser les
en-têtes d'authentification, l'environnement complet ou des exceptions contenant
une URL avec le jeton. Certains services, notamment Telegram, imposent un jeton
dans le chemin de leur API : construire cette URL en mémoire et la masquer dans
toute erreur, sans la placer dans le navigateur ou l'historique du terminal.

Distinguer authentification, autorisation et facturation. Une erreur de permission
ne démontre pas que le jeton est absent. Un refus de quota ne doit pas entraîner
des tentatives répétées sans limite. Ne pas contourner une protection pour tester
un accès.

## Recette GitHub Actions

Utiliser `secrets.NOM_DU_SECRET` dans le workflow et limiter sa transmission au
job ou à l'étape concernés. Séparer si possible le job du modèle, les écritures
GitHub et les notifications. Le modèle n'a pas besoin du jeton Telegram.

Les permissions de `GITHUB_TOKEN` et le réglage autorisant les PR de GitHub Actions
sont distincts des autorisations de la connexion GitHub du chat. Une erreur
`403 Resource not accessible by integration` de cette connexion ne se corrige
pas nécessairement en modifiant les permissions des workflows.

Si un appel payant est nécessaire, respecter le budget déjà autorisé et vérifier
la situation de facturation avant la planification. Ne pas garantir un plafond
à partir d'une alerte, d'un niveau de débit ou d'un solde prépayé. Pour une
interface ou une règle de facturation changeante, vérifier la documentation
officielle et l'état réellement visible. Si un bouton documenté est absent,
le signaler et adapter la procédure plutôt que répéter le même chemin.

## Exemple de configuration BROWT

| Nom GitHub | Valeur à fournir par l'utilisateur |
|---|---|
| `OPENAI_API_KEY` | Clé API créée dans le projet OpenAI destiné au worker |
| `TELEGRAM_BOT_TOKEN` | Jeton remis par BotFather après `/newbot` |
| `TELEGRAM_CHAT_ID` | Identifiant de la conversation privée destiné aux notifications |

Dans BROWT, `scripts/telegram_setup.py` est l'assistant **Telegram**, pas un
collecteur universel de clés. Sur Windows, il attend la copie du jeton, le lit
sans l'afficher, recherche les messages privés `/start` et copie l'identifiant
uniquement si une seule conversation est trouvée. Il n'envoie aucun message.
Son option `--manual` conserve la saisie masquée.

Pour un autre fournisseur, adapter le format de validation et l'opération de test.
Ne jamais réutiliser la validation d'un jeton Telegram pour une clé OpenAI.

## Références

- [Secrets GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
- [Jetons Telegram](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)
- [Gestion des clés de projet OpenAI](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform)
