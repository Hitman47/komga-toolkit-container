# Komga Toolkit WebUI container

Public container releases for the Komga Toolkit WebUI reference
`desktop-v2-20260722`.

The image is intended for Docker Compose and Portainer deployments:

```text
ghcr.io/hitman47/komga-toolkit-container:desktop-v2
```

Le tag `desktop-v2` est stable et pointe toujours vers la dernière publication
validée. Les tags versionnés, par exemple `2.2.0-desktop-v2`, restent disponibles
pour revenir à une version antérieure.

La version `2.2.0-desktop-v2` ajoute une API sécurisée permettant à une
application Android d'analyser puis de confirmer les mises à jour du suivi des
sorties via Manga News ou MangaBaka. Le conteneur attend un jeton d'au moins
24 caractères dans `KOMGA_TOOLKIT_AUTOMATION_TOKEN` (ou dans le fichier pointé
par `KOMGA_TOOLKIT_AUTOMATION_TOKEN_FILE`).

The application stores its own data in `/data`. No host directory is mounted by
the published Portainer stack.
