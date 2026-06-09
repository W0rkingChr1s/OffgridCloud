"""Registry of supported cloud-provider types.

Each type maps to an rclone backend plus a field schema. The schema drives:
  * the dynamic admin form (labels, input types, which fields are secret),
  * validation of submitted configs,
  * generation of rclone remote options.

Field ``key`` equals the rclone option name (1:1), so building an rclone remote
is just ``{"type": backend, **fixed, **config}``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str = "text"  # text | password | number | bool | textarea | select
    required: bool = True
    secret: bool = False
    help: str = ""
    default: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderType:
    key: str
    label: str
    backend: str
    fields: tuple[Field, ...]
    fixed: dict[str, str] = field(default_factory=dict)
    help: str = ""

    def to_rclone_options(self, config: dict[str, str]) -> dict[str, str]:
        opts: dict[str, str] = {"type": self.backend, **self.fixed}
        for f in self.fields:
            value = config.get(f.key, "")
            if value == "" or value is None:
                continue
            if f.type == "bool":
                truthy = str(value).lower() in ("true", "1", "yes", "on")
                opts[f.key] = "true" if truthy else "false"
            else:
                opts[f.key] = str(value)
        return opts


_HOST = Field("host", "Host", help="Hostname oder IP")
_USER = Field("user", "Benutzer")
_PASS = Field("pass", "Passwort", type="password", secret=True)

PROVIDER_TYPES: dict[str, ProviderType] = {
    "s3": ProviderType(
        key="s3",
        label="Amazon S3",
        backend="s3",
        fixed={"provider": "AWS"},
        fields=(
            Field("access_key_id", "Access Key ID"),
            Field("secret_access_key", "Secret Access Key", type="password", secret=True),
            Field("region", "Region", required=False, default="eu-central-1"),
            Field("endpoint", "Endpoint", required=False, help="Nur für S3-kompatible Dienste"),
        ),
    ),
    "minio": ProviderType(
        key="minio",
        label="MinIO",
        backend="s3",
        fixed={"provider": "Minio"},
        fields=(
            Field("access_key_id", "Access Key"),
            Field("secret_access_key", "Secret Key", type="password", secret=True),
            Field("endpoint", "Endpoint", help="z. B. https://minio.example.com"),
            Field("region", "Region", required=False, default="us-east-1"),
        ),
    ),
    "azureblob": ProviderType(
        key="azureblob",
        label="Azure Blob Storage",
        backend="azureblob",
        fields=(
            Field("account", "Storage Account"),
            Field("key", "Account Key", type="password", secret=True),
        ),
    ),
    "onedrive": ProviderType(
        key="onedrive",
        label="OneDrive / SharePoint",
        backend="onedrive",
        help="Token via 'rclone authorize onedrive' auf einem Rechner mit Browser erzeugen.",
        fields=(
            Field("token", "OAuth-Token (JSON)", type="textarea", secret=True,
                  help="Ausgabe von 'rclone authorize onedrive'"),
            Field("drive_id", "Drive ID", required=False),
            Field("drive_type", "Drive-Typ", type="select", required=False,
                  default="personal", options=("personal", "business", "documentLibrary")),
        ),
    ),
    "nextcloud": ProviderType(
        key="nextcloud",
        label="Nextcloud",
        backend="webdav",
        fixed={"vendor": "nextcloud"},
        fields=(
            Field("url", "WebDAV-URL",
                  help="z. B. https://cloud.example.com/remote.php/dav/files/USERNAME/"),
            _USER, _PASS,
        ),
    ),
    "owncloud": ProviderType(
        key="owncloud",
        label="ownCloud",
        backend="webdav",
        fixed={"vendor": "owncloud"},
        fields=(Field("url", "WebDAV-URL"), _USER, _PASS),
    ),
    "webdav": ProviderType(
        key="webdav",
        label="WebDAV (generisch)",
        backend="webdav",
        fields=(
            Field("url", "WebDAV-URL"),
            Field("vendor", "Vendor", type="select", required=False, default="other",
                  options=("other", "nextcloud", "owncloud", "sharepoint")),
            _USER, _PASS,
        ),
    ),
    "hetzner_storagebox": ProviderType(
        key="hetzner_storagebox",
        label="Hetzner Storage Box",
        backend="sftp",
        fixed={"port": "23"},
        help="Storage Box über SFTP (Port 23).",
        fields=(
            Field("host", "Host", help="z. B. uXXXXX.your-storagebox.de"),
            Field("user", "Benutzer", help="z. B. uXXXXX"),
            _PASS,
        ),
    ),
    "sftp": ProviderType(
        key="sftp",
        label="SFTP / SCP / SSH",
        backend="sftp",
        help="Auch für Synology/QNAP/TrueNAS via SFTP.",
        fields=(
            _HOST,
            Field("port", "Port", type="number", required=False, default="22"),
            _USER,
            Field("pass", "Passwort", type="password", required=False, secret=True),
            Field("key_pem", "SSH-Private-Key (PEM)", type="textarea", required=False, secret=True,
                  help="Alternativ zum Passwort"),
        ),
    ),
    "ftp": ProviderType(
        key="ftp",
        label="FTP / FTPS",
        backend="ftp",
        fields=(
            _HOST,
            Field("port", "Port", type="number", required=False, default="21"),
            _USER, _PASS,
            Field("tls", "FTPS (implizit)", type="bool", required=False),
            Field("explicit_tls", "FTPS (explizit)", type="bool", required=False),
        ),
    ),
    "smb": ProviderType(
        key="smb",
        label="SMB / NAS (Synology, QNAP, TrueNAS)",
        backend="smb",
        fields=(
            _HOST, _USER, _PASS,
            Field("domain", "Domäne", required=False, default="WORKGROUP"),
        ),
    ),
}


def get_type(key: str) -> ProviderType | None:
    return PROVIDER_TYPES.get(key)


def registry_json() -> list[dict]:
    """Serialise the registry for the frontend form (fixed options omitted)."""
    out = []
    for pt in PROVIDER_TYPES.values():
        out.append(
            {
                "key": pt.key,
                "label": pt.label,
                "help": pt.help,
                "fields": [asdict(f) for f in pt.fields],
            }
        )
    return out


def validate_config(pt: ProviderType, config: dict[str, str]) -> list[str]:
    """Return a list of missing required field labels (empty == valid)."""
    missing = []
    for f in pt.fields:
        if f.required and not str(config.get(f.key, "")).strip():
            missing.append(f.label)
    return missing
