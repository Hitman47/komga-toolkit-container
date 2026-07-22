# Komga Toolkit WebUI container

Public container releases for the Komga Toolkit WebUI reference
`desktop-v2-20260722`.

The image is intended for Docker Compose and Portainer deployments:

```text
ghcr.io/hitman47/komga-toolkit-container:desktop-v2
```

Le tag `desktop-v2` est stable et pointe toujours vers la dernière publication
validée. Les tags versionnés, par exemple `2.3.0-desktop-v2`, restent disponibles
pour revenir à une version antérieure.

La version `2.3.0-desktop-v2` fournit une API sécurisée permettant à une
application Android d'analyser puis de confirmer les mises à jour du suivi des
sorties et des prochaines sorties via Manga News ou MangaBaka. Le conteneur
attend un jeton d'au moins
24 caractères dans `KOMGA_TOOLKIT_AUTOMATION_TOKEN` (ou dans le fichier pointé
par `KOMGA_TOOLKIT_AUTOMATION_TOKEN_FILE`).

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

The application stores its own data in `/data`. No host directory is mounted by
the published Portainer stack.
