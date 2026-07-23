# Komga Toolkit WebUI container

Public container releases for the Komga Toolkit WebUI reference
`desktop-v3.12.0rc2-20260723`.

The image is intended for Docker Compose and Portainer deployments:

```text
ghcr.io/hitman47/komga-toolkit-container:desktop-v2
```

Le tag `desktop-v2` est stable et pointe toujours vers la dernière publication
validée. Les tags versionnés, par exemple `2.6.1-desktop-v2`, restent disponibles
pour revenir à une version antérieure.

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
