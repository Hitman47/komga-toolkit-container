# Komga Toolkit WebUI container

Public container releases for the Komga Toolkit WebUI reference
`desktop-v2-20260722`.

The image is intended for Docker Compose and Portainer deployments:

```text
ghcr.io/hitman47/komga-toolkit-container:desktop-v2
```

Le tag `desktop-v2` est stable et pointe toujours vers la dernière publication
validée. Les tags versionnés, par exemple `2.1.2-desktop-v2`, restent disponibles
pour revenir à une version antérieure.

The application stores its own data in `/data`. No host directory is mounted by
the published Portainer stack.
