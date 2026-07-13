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
    category: str = "object"
    description: str = ""
    popular: bool = False

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


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    description: str = ""
    icon: str = ""


# Ordered categories that group provider types in the setup wizard.
CATEGORIES: tuple[Category, ...] = (
    Category(
        "object",
        "Objektspeicher (S3 & Co.)",
        "Skalierbarer Cloud-Objektspeicher – ideal für große Backups.",
        icon="🪣",
    ),
    Category(
        "drive",
        "Cloud-Laufwerke",
        "Persönliche und geschäftliche Cloud-Dienste per OAuth.",
        icon="☁️",
    ),
    Category(
        "selfhosted",
        "Self-Hosted & NAS",
        "Eigene Server, Nextcloud/ownCloud und Netzwerkspeicher.",
        icon="🏠",
    ),
    Category(
        "protocol",
        "Server-Protokolle",
        "Direkter Zugriff über SFTP/SSH oder FTP.",
        icon="🔌",
    ),
)


_HOST = Field("host", "Host", help="Hostname oder IP")
_USER = Field("user", "Benutzer")
_PASS = Field("pass", "Passwort", type="password", secret=True)
_ACCESS_KEY = Field("access_key_id", "Access Key ID")
_SECRET_KEY = Field("secret_access_key", "Secret Access Key", type="password", secret=True)


def _s3_compatible(
    key: str,
    label: str,
    provider: str,
    *,
    description: str = "",
    endpoint_help: str = "",
    endpoint_default: str = "",
    region_default: str = "",
    region_required: bool = False,
    help: str = "",
    popular: bool = False,
) -> ProviderType:
    """Build an S3-compatible provider (shared shape for R2, Wasabi, Spaces, …)."""
    return ProviderType(
        key=key,
        label=label,
        backend="s3",
        category="object",
        description=description,
        popular=popular,
        help=help,
        fixed={"provider": provider},
        fields=(
            _ACCESS_KEY,
            _SECRET_KEY,
            Field(
                "endpoint",
                "Endpoint",
                required=bool(endpoint_help) and not endpoint_default,
                default=endpoint_default,
                help=endpoint_help,
            ),
            Field(
                "region",
                "Region",
                required=region_required,
                default=region_default,
            ),
        ),
    )

_PROVIDER_LIST: tuple[ProviderType, ...] = (
    # --- Object storage: S3 & compatible ----------------------------------
    ProviderType(
        key="s3",
        label="Amazon S3",
        backend="s3",
        category="object",
        description="AWS Simple Storage Service – der Standard für Objektspeicher.",
        popular=True,
        fixed={"provider": "AWS"},
        fields=(
            _ACCESS_KEY,
            _SECRET_KEY,
            Field("region", "Region", required=False, default="eu-central-1"),
            Field("endpoint", "Endpoint", required=False, help="Nur für S3-kompatible Dienste"),
            Field("storage_class", "Speicherklasse", type="select", required=False, default="",
                  options=("", "STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING",
                           "ONEZONE_IA", "GLACIER", "DEEP_ARCHIVE")),
        ),
    ),
    _s3_compatible(
        "cloudflare_r2", "Cloudflare R2", "Cloudflare",
        description="Objektspeicher ohne Egress-Gebühren.",
        endpoint_help="https://<accountid>.r2.cloudflarestorage.com",
        region_default="auto",
        popular=True,
    ),
    _s3_compatible(
        "backblaze_b2_s3", "Backblaze B2 (S3)", "Other",
        description="Günstiger Cloud-Speicher über die S3-kompatible API.",
        endpoint_help="z. B. s3.eu-central-003.backblazeb2.com",
    ),
    _s3_compatible(
        "wasabi", "Wasabi", "Wasabi",
        description="Hot Cloud Storage zum Pauschalpreis.",
        endpoint_default="s3.wasabisys.com",
        endpoint_help="Regionaler Endpoint, z. B. s3.eu-central-1.wasabisys.com",
    ),
    _s3_compatible(
        "digitalocean_spaces", "DigitalOcean Spaces", "DigitalOcean",
        description="S3-kompatibler Objektspeicher von DigitalOcean.",
        endpoint_help="z. B. fra1.digitaloceanspaces.com",
    ),
    _s3_compatible(
        "idrive_e2", "IDrive e2", "IDrive",
        description="Preiswerter S3-kompatibler Objektspeicher.",
        endpoint_help="z. B. p4de.eu.idrivee2-XX.com",
    ),
    _s3_compatible(
        "scaleway", "Scaleway Object Storage", "Scaleway",
        description="Europäischer S3-kompatibler Speicher.",
        endpoint_default="s3.fr-par.scw.cloud",
        region_default="fr-par",
    ),
    _s3_compatible(
        "storj", "Storj (S3-Gateway)", "Storj",
        description="Dezentraler, verschlüsselter Speicher.",
        endpoint_default="gateway.storjshare.io",
    ),
    ProviderType(
        key="backblaze_b2",
        label="Backblaze B2 (nativ)",
        backend="b2",
        category="object",
        description="Native B2-API mit Application Keys.",
        popular=True,
        help="Application Key im Backblaze-Konto unter 'App Keys' erstellen.",
        fields=(
            Field("account", "Key ID (Account ID)"),
            Field("key", "Application Key", type="password", secret=True),
            Field("hard_delete", "Alte Versionen endgültig löschen", type="bool", required=False),
        ),
    ),
    ProviderType(
        key="gcs",
        label="Google Cloud Storage",
        backend="gcs",
        category="object",
        description="Objektspeicher der Google Cloud Platform.",
        help="Service-Account-Schlüssel (JSON) in der Google Cloud Console erstellen.",
        fields=(
            Field("service_account_credentials", "Service-Account (JSON)", type="textarea",
                  secret=True, help="Inhalt der Schlüssel-JSON-Datei"),
            Field("project_number", "Projekt-Nummer", required=False),
            Field("location", "Region", required=False, default="eu",
                  help="Standort für neue Buckets, z. B. eu, us, europe-west3"),
            Field("bucket_policy_only", "Uniform Bucket-Level Access", type="bool", required=False),
        ),
    ),
    ProviderType(
        key="azureblob",
        label="Azure Blob Storage",
        backend="azureblob",
        category="object",
        description="Objektspeicher von Microsoft Azure.",
        fields=(
            Field("account", "Storage Account"),
            Field("key", "Account Key", type="password", secret=True),
        ),
    ),
    ProviderType(
        key="minio",
        label="MinIO",
        backend="s3",
        category="object",
        description="Selbst gehosteter S3-kompatibler Objektspeicher.",
        fixed={"provider": "Minio"},
        fields=(
            Field("access_key_id", "Access Key"),
            Field("secret_access_key", "Secret Key", type="password", secret=True),
            Field("endpoint", "Endpoint", help="z. B. https://minio.example.com"),
            Field("region", "Region", required=False, default="us-east-1"),
        ),
    ),
    # --- Cloud drives (OAuth) ---------------------------------------------
    ProviderType(
        key="googledrive",
        label="Google Drive",
        backend="drive",
        category="drive",
        description="Persönliches oder geschäftliches Google Drive.",
        popular=True,
        help="Token via 'rclone authorize drive' auf einem Rechner mit Browser erzeugen.",
        fields=(
            Field("token", "OAuth-Token (JSON)", type="textarea", secret=True,
                  help="Ausgabe von 'rclone authorize drive'"),
            Field("root_folder_id", "Root-Ordner-ID", required=False,
                  help="Optional: nur in diesen Ordner synchronisieren"),
            Field("team_drive", "Team-/Shared-Drive-ID", required=False),
            Field("client_id", "Client ID (optional)", required=False,
                  help="Eigene OAuth-Credentials für höhere Limits"),
            Field("client_secret", "Client Secret (optional)", type="password",
                  required=False, secret=True),
        ),
    ),
    ProviderType(
        key="dropbox",
        label="Dropbox",
        backend="dropbox",
        category="drive",
        description="Dropbox-Konto (privat oder Business).",
        popular=True,
        help="Token via 'rclone authorize dropbox' erzeugen.",
        fields=(
            Field("token", "OAuth-Token (JSON)", type="textarea", secret=True,
                  help="Ausgabe von 'rclone authorize dropbox'"),
            Field("client_id", "App Key (optional)", required=False),
            Field("client_secret", "App Secret (optional)", type="password",
                  required=False, secret=True),
        ),
    ),
    ProviderType(
        key="onedrive",
        label="OneDrive / SharePoint",
        backend="onedrive",
        category="drive",
        description="Microsoft OneDrive und SharePoint-Bibliotheken.",
        popular=True,
        help="Token via 'rclone authorize onedrive' auf einem Rechner mit Browser erzeugen.",
        fields=(
            Field("token", "OAuth-Token (JSON)", type="textarea", secret=True,
                  help="Ausgabe von 'rclone authorize onedrive'"),
            Field("drive_id", "Drive ID", required=False),
            Field("drive_type", "Drive-Typ", type="select", required=False,
                  default="personal", options=("personal", "business", "documentLibrary")),
        ),
    ),
    ProviderType(
        key="box",
        label="Box",
        backend="box",
        category="drive",
        description="Box.com Cloud-Speicher.",
        help="Token via 'rclone authorize box' erzeugen.",
        fields=(
            Field("token", "OAuth-Token (JSON)", type="textarea", secret=True,
                  help="Ausgabe von 'rclone authorize box'"),
        ),
    ),
    ProviderType(
        key="pcloud",
        label="pCloud",
        backend="pcloud",
        category="drive",
        description="pCloud – europäischer Cloud-Speicher.",
        help="Token via 'rclone authorize pcloud' erzeugen.",
        fields=(
            Field("token", "OAuth-Token (JSON)", type="textarea", secret=True,
                  help="Ausgabe von 'rclone authorize pcloud'"),
            Field("hostname", "API-Host", type="select", required=False,
                  default="api.pcloud.com",
                  options=("api.pcloud.com", "eapi.pcloud.com"),
                  help="eapi.pcloud.com für EU-Konten"),
        ),
    ),
    ProviderType(
        key="mega",
        label="MEGA",
        backend="mega",
        category="drive",
        description="MEGA.nz – Ende-zu-Ende-verschlüsselter Speicher.",
        fields=(
            Field("user", "E-Mail"),
            _PASS,
        ),
    ),
    # --- Self-hosted & NAS -------------------------------------------------
    ProviderType(
        key="nextcloud",
        label="Nextcloud",
        backend="webdav",
        category="selfhosted",
        description="Nextcloud-Instanz über WebDAV.",
        popular=True,
        fixed={"vendor": "nextcloud"},
        fields=(
            Field("url", "WebDAV-URL",
                  help="z. B. https://cloud.example.com/remote.php/dav/files/USERNAME/"),
            _USER, _PASS,
        ),
    ),
    ProviderType(
        key="owncloud",
        label="ownCloud",
        backend="webdav",
        category="selfhosted",
        description="ownCloud-Instanz über WebDAV.",
        fixed={"vendor": "owncloud"},
        fields=(Field("url", "WebDAV-URL"), _USER, _PASS),
    ),
    ProviderType(
        key="webdav",
        label="WebDAV (generisch)",
        backend="webdav",
        category="selfhosted",
        description="Beliebiger WebDAV-Server.",
        fields=(
            Field("url", "WebDAV-URL"),
            Field("vendor", "Vendor", type="select", required=False, default="other",
                  options=("other", "nextcloud", "owncloud", "sharepoint")),
            _USER, _PASS,
        ),
    ),
    ProviderType(
        key="smb",
        label="SMB / NAS (Synology, QNAP, TrueNAS)",
        backend="smb",
        category="selfhosted",
        description="Netzwerkfreigabe per SMB/CIFS.",
        help="Nur Hostname/IP ins Host-Feld – den Port separat eintragen (Standard 445).",
        fields=(
            Field("host", "Host", help="Nur Hostname oder IP, ohne Port (z. B. 192.168.178.5)"),
            Field("port", "Port", type="number", required=False, default="445",
                  help="SMB-Port, Standard 445 (nicht der DSM-Weboberflächen-Port)"),
            _USER, _PASS,
            Field("domain", "Domäne", required=False, default="WORKGROUP"),
        ),
    ),
    ProviderType(
        key="hetzner_storagebox",
        label="Hetzner Storage Box",
        backend="sftp",
        category="selfhosted",
        description="Hetzner Storage Box über SFTP (Port 23).",
        fixed={"port": "23"},
        help="Storage Box über SFTP (Port 23).",
        fields=(
            Field("host", "Host", help="z. B. uXXXXX.your-storagebox.de"),
            Field("user", "Benutzer", help="z. B. uXXXXX"),
            _PASS,
        ),
    ),
    # --- Server protocols --------------------------------------------------
    ProviderType(
        key="sftp",
        label="SFTP / SCP / SSH",
        backend="sftp",
        category="protocol",
        description="Direkter Zugriff über SSH/SFTP.",
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
    ProviderType(
        key="ftp",
        label="FTP / FTPS",
        backend="ftp",
        category="protocol",
        description="Klassischer FTP- bzw. FTPS-Server.",
        fields=(
            _HOST,
            Field("port", "Port", type="number", required=False, default="21"),
            _USER, _PASS,
            Field("tls", "FTPS (implizit)", type="bool", required=False),
            Field("explicit_tls", "FTPS (explizit)", type="bool", required=False),
        ),
    ),
)

PROVIDER_TYPES: dict[str, ProviderType] = {pt.key: pt for pt in _PROVIDER_LIST}


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
                "category": pt.category,
                "description": pt.description,
                "popular": pt.popular,
                "fields": [asdict(f) for f in pt.fields],
            }
        )
    return out


def categories_json() -> list[dict]:
    """Serialise the ordered wizard categories for the frontend."""
    return [asdict(c) for c in CATEGORIES]


def validate_config(pt: ProviderType, config: dict[str, str]) -> list[str]:
    """Return a list of missing required field labels (empty == valid)."""
    missing = []
    for f in pt.fields:
        if f.required and not str(config.get(f.key, "")).strip():
            missing.append(f.label)
    return missing
