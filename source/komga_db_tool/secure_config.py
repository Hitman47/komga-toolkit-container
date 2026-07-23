from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol


SERVICE_NAME = "komga-toolkit"
SECRET_FIELDS = {
    "api_key": "komga.api_key",
    "username": "komga.username",
    "password": "komga.password",
    "comicvine_api_key": "comicvine.api_key",
}
SECRET_SPECS = (
    ("komga", "api_key", "komga.api_key"),
    ("komga", "username", "komga.username"),
    ("komga", "password", "komga.password"),
    ("manga_news", "token", "manga_news.token"),
    ("comicvine", "api_key", "comicvine.api_key"),
)


class SecureConfigError(RuntimeError):
    pass


class SecretVault(Protocol):
    def get(self, name: str) -> str:
        ...

    def set(self, name: str, value: str) -> None:
        ...

    def delete(self, name: str) -> None:
        ...


class SystemKeyringVault:
    """Store secrets in the credential vault of the current OS account."""

    @staticmethod
    def _keyring() -> Any:
        try:
            import keyring
        except ImportError as exc:
            raise SecureConfigError(
                "Le paquet keyring est requis pour enregistrer les identifiants de façon sécurisée."
            ) from exc
        return keyring

    def get(self, name: str) -> str:
        try:
            return self._keyring().get_password(SERVICE_NAME, name) or ""
        except Exception as exc:
            raise SecureConfigError("Impossible de lire le coffre de secrets système.") from exc

    def set(self, name: str, value: str) -> None:
        try:
            self._keyring().set_password(SERVICE_NAME, name, value)
        except Exception as exc:
            raise SecureConfigError("Impossible d'écrire dans le coffre de secrets système.") from exc

    def delete(self, name: str) -> None:
        keyring = self._keyring()
        try:
            keyring.delete_password(SERVICE_NAME, name)
        except keyring.errors.PasswordDeleteError:
            return
        except Exception as exc:
            raise SecureConfigError("Impossible de supprimer une valeur du coffre de secrets système.") from exc


class SecureConfigStore:
    def __init__(self, path: Path, vault: SecretVault | None = None):
        self.path = path
        self.vault = vault or SystemKeyringVault()

    @classmethod
    def default(cls) -> "SecureConfigStore":
        return cls(Path.cwd() / "config.json")

    def load(self, include_secrets: bool = True) -> dict[str, Any] | None:
        public: dict[str, Any]
        if not self.path.exists():
            public = {}
        else:
            try:
                public = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SecureConfigError("Le fichier config.json est illisible ou invalide.") from exc
        if not isinstance(public, dict):
            raise SecureConfigError("Le fichier config.json doit contenir un objet JSON.")

        data = deepcopy(public)
        refs = data.pop("secret_refs", {})
        if not isinstance(refs, dict):
            refs = {}
        for section_name, field, default_ref in SECRET_SPECS:
            section = data.setdefault(section_name, {})
            if not isinstance(section, dict):
                raise SecureConfigError(
                    f"La section {section_name} de config.json est invalide."
                )
            if not include_secrets:
                section[field] = ""
                continue
            ref = str(refs.get(default_ref) or refs.get(field) or default_ref)
            section[field] = self.vault.get(ref)
        return data

    def save(self, data: dict[str, Any]) -> Path:
        public = deepcopy(data)
        refs: dict[str, str] = {}
        secret_values: dict[str, str] = {}
        for section_name, field, ref in SECRET_SPECS:
            section = public.setdefault(section_name, {})
            if not isinstance(section, dict):
                raise SecureConfigError(
                    f"La configuration {section_name} est invalide."
                )
            refs[ref] = ref
            secret_values[ref] = str(section.pop(field, "") or "")

        for ref, value in secret_values.items():
            if value:
                self.vault.set(ref, value)
            # Un champ vide peut signifier que le fichier public a disparu ou
            # que le coffre n'a pas encore été chargé. Ne jamais effacer un
            # secret existant implicitement lors d'une sauvegarde/fermeture.

        public["schema_version"] = 2
        public["secret_storage"] = "system_keyring"
        public["secret_refs"] = refs
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        except OSError as exc:
            raise SecureConfigError("Impossible d'écrire config.json.") from exc
        return self.path
