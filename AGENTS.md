# BROWT

Lire `CONTRIBUTING.md`, la feuille de route `docs/roadmap.qmd` et le journal
`docs/progress.qmd` avant de commencer un nouveau jalon. Préserver les originaux
des données et distinguer résultats synthétiques, essais techniques et mesures
scientifiques.

## Clés et accès externes

Si une tâche nécessite une clé API, un jeton, un secret CI, l'ajout d'un secret
GitHub ou la correction d'une saisie de clé, lire et appliquer
[secret-handoff](.agents/skills/secret-handoff/SKILL.md) avant de demander une action
à l'utilisateur.

Utiliser les secrets GitHub pour GitHub Actions et un assistant local sans
collage dans le terminal pour une saisie Windows nécessaire.
`scripts/telegram_setup.py` gère uniquement les jetons Telegram et l'identifiant
de conversation. Ne pas demander la valeur d'une clé dans le chat.

Les permissions du connecteur GitHub du chat et celles de `GITHUB_TOKEN` dans
les workflows sont distinctes. Vérifier les résultats réels avant de déclarer
une intégration opérationnelle ; documenter séparément les déclarations et les
tests. Ne pas réclamer une nouvelle autorisation pour une action déjà autorisée.
