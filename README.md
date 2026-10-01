# Komga Toolkit WebUI container

Public container releases for the Komga Toolkit WebUI reference
`desktop-v3.12.0rc52-20261001` (Web API and bundle `2.52.0`).

The image is intended for Docker Compose and Portainer deployments:

```text
ghcr.io/hitman47/komga-toolkit-container:desktop-v2
```

La publication est reconstruite par GitHub Actions depuis le snapshot rc52
conservé dans `source/`. Docker Desktop n'est pas nécessaire à la construction.
Avant publication, le conteneur est démarré sans données utilisateur, avec un
système de fichiers en lecture seule : santé, référence et bundle Web doivent
répondre et annoncer la même version. Un échec empêche la publication.
Ce snapshot ne contient que le Dockerfile, les roues/dépendances Docker et le
code applicatif nécessaire à l’image. Il ne contient ni configuration locale,
ni identifiant, ni exécutable Desktop.

Le tag `desktop-v2` est stable et pointe toujours vers la dernière publication
validée. Les tags versionnés `2.52.0` et `2.52.0-desktop-v2` restent disponibles
pour revenir à une version antérieure.

Pour Portainer, utilisez `docker-compose.portainer.yml` ou remplacez l'image
du stack existant par `ghcr.io/hitman47/komga-toolkit-container:2.52.0`, puis
recréez le service en conservant son volume `/data`. La publication d'une image
ne met pas à jour automatiquement les conteneurs déjà déployés.

Cette version inclut les catalogues de secours, les fichiers sources centralisés,
le rafraîchissement explicite des inventaires et le choix de 500 lignes pour les
écrans de sorties. Le nouveau thème rc52 reste propre à l'application Desktop.

## Historique des versions précédentes

Les routes d'automatisation Bedetheque utilisent exclusivement le CSV persistant
`/data/uploads/bedetheque.csv`. Elles ne contactent jamais le site Bedetheque et
refusent de démarrer tant que le CSV n'est pas disponible. Le contrat HTTP
reste inchangé. Ces lectures étant locales, aucun délai n'est appliqué entre
deux séries Bedetheque. Le fichier reste dans le volume `/data` après
rafraîchissement, reconnexion ou recréation du conteneur. Un nouvel upload
valide le remplace atomiquement ; un fichier invalide ne détruit pas la copie
existante.

La version `2.24.0-desktop-v2` aligne la WebUI et l’image Docker sur Komga
Toolkit Desktop `3.12.0rc24`. Elle embarque les écrans et correctifs de la rc24,
ainsi que l’enrichissement **Metron** mono-série : jeton masqué, recherche,
comparaison, prévisualisation et sauvegarde avant écriture. Les traitements
Metron multiples et par tome restent volontairement exclus de cette étape.

La version `2.7.0-desktop-v2` aligne Desktop et Web sur la validation
d’enrichissement des tomes : titre et titre de tri décochés par défaut, sélection
groupée des confiances élevées, et prise en charge du nombre de pages fourni par
Manga News ou Bedetheque.

La version `2.6.1-desktop-v2` fournit une API sécurisée permettant à une
application Android d'analyser puis de confirmer les mises à jour du suivi des
sorties et des prochaines sorties via Manga News ou MangaBaka, ainsi que le
suivi haute confiance des tomes via Bedetheque ou ComicVine. Le conteneur attend
un jeton d'au moins
24 caractères dans `KOMGA_TOOLKIT_AUTOMATION_TOKEN` (ou dans le fichier pointé
par `KOMGA_TOOLKIT_AUTOMATION_TOKEN_FILE`).

La version 2.6.1 fiabilise le chargement des grandes bibliothèques dans Manga
News et dans les autres écrans d'enrichissement partagés. Les lectures
d'historique utilisent des POST bornés par lots de 500, leur échec ne masque
plus les séries Komga, et la page Web détecte automatiquement un bundle ancien
après redéploiement. La page de démarrage est servie sans cache.

La version 2.6.0 aligne la WebUI sur Komga Toolkit Desktop 3.12.0rc2 et ajoute
le sous-onglet `Tous les tomes` à l'Explorateur : recherche, filtres par
bibliothèque, date d'ajout Komga, langue, statut, source et métadonnées
manquantes, tris, sélection multiple et enrichissement par Manga News,
Bedetheque ou ComicVine. Desktop et Web partagent désormais les mêmes
garde-fous : numéro, ordre numérique et ISBN protégés, résumé de faible qualité
ignoré et validation explicite des correspondances ambiguës.

La version 2.5.0 ajoute les routes `/run`, `/preview` et `/confirm` pour
Bedetheque et ComicVine. Les traitements restent limités aux séries non
terminées déjà liées à la source, refusent toute diminution ou hausse trop
rapide de `totalBookCount`, revalident Komga avant écriture et ne renvoient dans
`rows` que les changements réellement appliqués. Elle restaure également les
genres réels fournis par Manga News V2.

La version 2.2.1 corrige la classification Manga News lorsque `media_kind`
n'est pas renvoyé et distingue désormais clairement `À vérifier` de
`À appliquer` dans les résultats d'automatisation.

La version 2.2.2 permet au conteneur de se connecter automatiquement à un
serveur Komga unique avec `KOMGA_BASE_URL` et `KOMGA_API_KEY` ou
`KOMGA_API_KEY_FILE`. Les commandes externes ne dépendent alors plus d'une
session ouverte dans la WebUI.

La version 2.2.3 ne renvoie dans `rows` que les changements réellement valides
à confiance élevée. Les diminutions, hausses trop rapides, non-changements,
erreurs et exclusions restent bloqués et n'apparaissent plus comme lignes à
confirmer.

La version 2.3.0 ajoute les quatre routes externes `Prochaines sorties` avec le
même parcours aperçu, confirmation explicite et revalidation que le suivi des
sorties. Seuls les tags datés, différents et encore futurs sont proposés ; les
autres tags Komga sont conservés.

La version 2.4.0 ajoute quatre routes `/run` qui analysent, revalident et
appliquent automatiquement en une seule tâche, sans confirmation après le
déclenchement. Le suivi des sorties n'applique que les changements à confiance
élevée. Le résultat `rows` contient uniquement les changements effectivement
écrits, avec un contrat minimal.

La version 2.4.1 applique un limiteur anti-ban partagé à tous les appels Web
Manga News et MangaBaka. Le délai par défaut est d'une seconde entre deux
appels, y compris lorsque plusieurs jobs sont lancés en parallèle. Il est
configurable avec `MANGA_NEWS_AUTOMATION_DELAY_SECONDS` et
`MANGABAKA_AUTOMATION_DELAY_SECONDS`, sans possibilité de descendre sous le
minimum de sécurité.

The application stores its own data in `/data`. No host directory is mounted by
the published Portainer stack.
