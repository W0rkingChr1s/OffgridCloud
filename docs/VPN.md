# VPN-Client (WireGuard / OpenVPN)

Läuft OffgridCloud außerhalb des Heimnetzes (z. B. mobil im Feld) und soll ein
**internes Ziel** erreichen — etwa ein NAS, das nur per SMB unter einer privaten
`192.168.x.y` erreichbar ist —, kann sich die Box per **VPN** ins Heimnetz
einwählen. Steht der Tunnel, funktioniert ein SMB-Provider auf die private IP
ganz normal.

Verwaltung im Web-UI unter **VPN** (nur Admin): WireGuard- oder OpenVPN-Profil
einfügen, verbinden/trennen, Live-Status. Configs (mit privaten Schlüsseln)
werden verschlüsselt gespeichert und nie an den Browser zurückgegeben.

## Warum erhöhte Rechte nötig sind

Einen Tunnel aufzubauen ist eine **System-Operation**: Es entsteht ein
TUN-Interface und die Routing-Tabelle wird verändert. Das erfordert die
Capability **`CAP_NET_ADMIN`** und Zugriff auf **`/dev/net/tun`**. Der
OffgridCloud-Dienst läuft bewusst unprivilegiert, also müssen diese Rechte
gezielt erteilt werden. Wie, hängt vom Deployment ab — die VPN-Seite erkennt die
Umgebung und zeigt den passenden Weg an.

### Nativer Betrieb (systemd auf dem Raspberry Pi) — der Standard

Einmalig auf dem Server ausführen:

```bash
sudo /opt/offgridcloud/src/deploy/vpn/install.sh
```

Das Skript

1. installiert `wireguard-tools` und `openvpn`,
2. lädt das `tun`-Kernelmodul (sofort und dauerhaft via `/etc/modules-load.d`),
3. legt ein systemd-Drop-in an, das dem Dienst `CAP_NET_ADMIN` gibt
   (`/etc/systemd/system/offgridcloud.service.d/10-vpn-caps.conf`), und startet
   den Dienst neu.

Alternativ direkt bei der Installation:

```bash
sudo ./deploy/install.sh --start --with-vpn
```

Danach die VPN-Seite neu laden — der Hinweis „erhöhte Rechte" verschwindet.

### Docker

Dem Container die Rechte beim Start mitgeben:

```bash
docker run --cap-add=NET_ADMIN --device=/dev/net/tun … offgridcloud
```

docker-compose: `cap_add: [NET_ADMIN]` und `devices: ["/dev/net/tun"]`.

## Grenzen

- **Kein DNS über den Tunnel.** Mit nur `CAP_NET_ADMIN` (ohne vollen Root) kann
  `wg-quick` den System-Resolver nicht umschreiben. Lass in der
  WireGuard-Config die Zeile `DNS = …` weg und adressiere interne Ziele **per
  IP** (z. B. `192.168.178.20`). OpenVPN-Profile, die `resolv.conf` verändern
  wollen, sind entsprechend eingeschränkt.
- **Ein Tunnel gleichzeitig.** Es gibt genau einen Default-Pfad ins Remote-LAN;
  ein neuer Connect trennt einen bereits aktiven Tunnel.
- **FRITZ!Box-Tipp.** Die WireGuard-Config aus der FRITZ!Box (WireGuard-Verbindung
  → *Konfiguration anzeigen*) lässt sich direkt einfügen; die `DNS`-Zeile wie oben
  entfernen.

## Fehlersuche

| Symptom | Ursache / Lösung |
|---------|------------------|
| „VPN benötigt erhöhte Rechte" bleibt nach dem Skript | Dienst nicht neu gestartet: `sudo systemctl restart offgridcloud`. Bei manuellem Start (uvicorn) greift das Drop-in nicht — dann als root/mit Capability starten. |
| „kein /dev/net/tun" | `sudo modprobe tun`; ggf. Reboot. Auf manchen Kerneln ist `tun` fest eingebaut und das Gerät erscheint erst nach Neustart. |
| Verbindung steht, NAS aber nicht erreichbar | Prüfen, ob in der Config `AllowedIPs` das Ziel-Subnetz enthält, und ob per IP (nicht Hostname) zugegriffen wird. |
