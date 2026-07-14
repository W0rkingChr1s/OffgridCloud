from app import vpn as vpnsvc

WG_CONFIG = (
    "[Interface]\n"
    "PrivateKey = aVeryPrivateKeyThatMustStaySecret=\n"
    "Address = 10.0.0.2/32\n\n"
    "[Peer]\n"
    "PublicKey = somePublicKey=\n"
    "Endpoint = home.example.net:51820\n"
    "AllowedIPs = 192.168.178.0/24\n"
)


def _create(client, admin_auth, name="Zuhause", type="wireguard", **extra):
    body = {"name": name, "type": type, "config": WG_CONFIG}
    body.update(extra)
    return client.post("/api/vpn", headers=admin_auth, json=body)


# --- Capability logic (pure) ---------------------------------------------


def test_capabilities_supports_and_blocker():
    caps = vpnsvc.Capabilities(net_admin=True, tun_device=True, wireguard=True, openvpn=False)
    assert caps.supports("wireguard") is True
    assert caps.supports("openvpn") is False
    assert caps.blocker("wireguard") == ""
    assert "openvpn" in caps.blocker("openvpn").lower()

    missing = vpnsvc.Capabilities(net_admin=False, tun_device=False, wireguard=True, openvpn=True)
    assert missing.supports("wireguard") is False
    assert "tun" in missing.blocker("wireguard").lower()


def test_blocker_is_environment_aware():
    missing = vpnsvc.Capabilities(net_admin=False, tun_device=True, wireguard=True, openvpn=True)
    docker_msg = missing.blocker("wireguard", docker=True)
    native_msg = missing.blocker("wireguard", docker=False)
    assert "cap-add" in docker_msg.lower()
    assert "cap-add" not in native_msg.lower()
    # The native remediation points at the enable helper, not Docker flags.
    assert "install.sh" in native_msg


def test_parse_wg_config_splits_wgquick_directives():
    config = (
        "[Interface]\n"
        "PrivateKey = privkey=\n"
        "Address = 10.0.0.2/32, fd00::2/128\n"
        "DNS = 192.168.178.1\n"
        "MTU = 1420\n"
        "Table = off\n"
        "PostUp = echo hi\n\n"
        "[Peer]\n"
        "PublicKey = pubkey=\n"
        "Endpoint = home.example.net:51820\n"
        "AllowedIPs = 192.168.178.0/24, 10.0.0.0/24\n"
        "PersistentKeepalive = 25\n"
    )
    wg_conf, addresses, mtu, routes = vpnsvc.parse_wg_config(config)

    # Interface addressing / MTU / routes are extracted for `ip` to apply.
    assert addresses == ["10.0.0.2/32", "fd00::2/128"]
    assert mtu == "1420"
    assert routes == ["192.168.178.0/24", "10.0.0.0/24"]

    # wg-quick-only directives must be gone from what `wg setconf` receives...
    assert "DNS" not in wg_conf
    assert "Address" not in wg_conf
    assert "MTU" not in wg_conf
    assert "Table" not in wg_conf
    assert "PostUp" not in wg_conf
    # ...while the kernel-understood keys (incl. AllowedIPs) are preserved.
    assert "PrivateKey = privkey=" in wg_conf
    assert "PublicKey = pubkey=" in wg_conf
    assert "Endpoint = home.example.net:51820" in wg_conf
    assert "AllowedIPs = 192.168.178.0/24, 10.0.0.0/24" in wg_conf


def test_in_docker_respects_override(monkeypatch):
    monkeypatch.setenv("OGC_IN_DOCKER", "true")
    assert vpnsvc.in_docker() is True
    monkeypatch.setenv("OGC_IN_DOCKER", "0")
    assert vpnsvc.in_docker() is False


def test_capabilities_endpoint(client, admin_auth):
    caps = client.get("/api/vpn/capabilities", headers=admin_auth).json()
    for key in (
        "net_admin", "tun_device", "wireguard", "openvpn",
        "ready", "message", "docker", "enable_command",
    ):
        assert key in caps
    # ready == base requirements; message set only when not ready.
    assert caps["ready"] == (caps["net_admin"] and caps["tun_device"])
    # Native (non-Docker) hosts advertise the enable helper.
    if not caps["docker"] and not caps["ready"]:
        assert "install.sh" in caps["enable_command"]


# --- CRUD -----------------------------------------------------------------


def test_create_does_not_return_config(client, admin_auth):
    resp = _create(client, admin_auth)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Zuhause"
    assert body["type"] == "wireguard"
    assert body["active"] is False
    # The raw config / private key must never come back to the client.
    assert "config" not in body
    assert "PrivateKey" not in str(body)


def test_config_encrypted_at_rest(client, admin_auth):
    _create(client, admin_auth, name="EncVpn")
    from app.db import SessionLocal
    from app.models import VpnTunnel

    with SessionLocal() as db:
        t = db.query(VpnTunnel).filter_by(name="EncVpn").one()
        assert "aVeryPrivateKeyThatMustStaySecret" not in t.config_encrypted


def test_list_and_openvpn_username_flag(client, admin_auth):
    _create(client, admin_auth, name="WG only")
    _create(
        client, admin_auth, name="OVPN", type="openvpn",
        username="alice", password="s3cret",
    )
    tunnels = client.get("/api/vpn", headers=admin_auth).json()
    by_name = {t["name"]: t for t in tunnels}
    assert by_name["WG only"]["has_username"] is False
    assert by_name["OVPN"]["has_username"] is True


def test_update_keeps_config_when_omitted(client, admin_auth):
    tid = _create(client, admin_auth, name="Keep").json()["id"]
    client.patch(f"/api/vpn/{tid}", headers=admin_auth, json={"name": "Renamed", "autostart": True})
    import json as _json

    from app.crypto import decrypt
    from app.db import SessionLocal
    from app.models import VpnTunnel

    with SessionLocal() as db:
        t = db.get(VpnTunnel, tid)
        assert t.name == "Renamed"
        assert t.autostart is True
        cfg = _json.loads(decrypt(t.config_encrypted))
        assert "PrivateKey" in cfg["config"]  # config preserved


def test_connect_reports_missing_privileges(client, admin_auth, monkeypatch):
    tid = _create(client, admin_auth).json()["id"]
    # Force an environment without the required capabilities.
    monkeypatch.setattr(
        vpnsvc, "capabilities",
        lambda: vpnsvc.Capabilities(
            net_admin=False, tun_device=False, wireguard=True, openvpn=True
        ),
    )
    resp = client.post(f"/api/vpn/{tid}/connect", headers=admin_auth)
    assert resp.status_code == 400
    assert "tun" in resp.json()["detail"].lower() or "net_admin" in resp.json()["detail"].lower()
    # The failure is persisted as last_error.
    t = next(x for x in client.get("/api/vpn", headers=admin_auth).json() if x["id"] == tid)
    assert t["active"] is False
    assert t["last_error"]


def test_delete_tunnel(client, admin_auth):
    tid = _create(client, admin_auth, name="Del").json()["id"]
    assert client.delete(f"/api/vpn/{tid}", headers=admin_auth).status_code == 204
    remaining = {t["id"] for t in client.get("/api/vpn", headers=admin_auth).json()}
    assert tid not in remaining


def test_status_endpoint_down_by_default(client, admin_auth):
    st = client.get("/api/vpn/status", headers=admin_auth).json()
    assert st["state"] == "down"
    assert st["active_id"] is None


def test_non_admin_cannot_access_vpn(client, admin_auth):
    client.post(
        "/api/users", headers=admin_auth,
        json={"email": "v@test.local", "password": "userpass123"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "v@test.local", "password": "userpass123"}
    ).json()["access_token"]
    resp = client.get("/api/vpn", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
