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

Alternativ direkt bei der Installation — die Frage „VPN-Client … einrichten?"
mit ja beantworten (oder `OGC_WITH_VPN=1` für eine unbeaufsichtigte Installation):

```bash
sudo ./deploy/install.sh
```

Danach die VPN-Seite neu laden — der Hinweis „erhöhte Rechte" verschwindet.

### Docker

Dem Container die Rechte beim Start mitgeben:

```bash
docker run --cap-add=NET_ADMIN --device=/dev/net/tun … offgridcloud
```

docker-compose: `cap_add: [NET_ADMIN]` und `devices: ["/dev/net/tun"]`.

## Grenzen

- **Kein DNS über den Tunnel.** WireGuard-Tunnel werden bewusst ohne `wg-quick`
  direkt über `ip`/`wg` aufgebaut (siehe unten) — der System-Resolver wird dabei
  nicht angefasst. Lass in der WireGuard-Config die Zeile `DNS = …` weg (sie wird
  ohnehin ignoriert) und adressiere interne Ziele **per IP** (z. B.
  `192.168.178.20`). OpenVPN-Profile, die `resolv.conf` verändern wollen, sind
  mit nur `CAP_NET_ADMIN` entsprechend eingeschränkt.
- **Nur Split-Tunnel.** Es werden Routen für die `AllowedIPs`-Subnetze gesetzt.
  Ein Voll-Tunnel (`AllowedIPs = 0.0.0.0/0`) bräuchte die fwmark-Policy-Routen,
  die `wg-quick` nur als echter Root anlegt; solche Einträge werden übersprungen.
  Trage das Ziel-Subnetz ein (z. B. `192.168.178.0/24`), nicht `0.0.0.0/0`.
- **Lokaler Zugriff hat Vorrang (Subnetz-Kollision).** Überschneidet sich ein
  `AllowedIPs`-Subnetz mit dem **eigenen lokalen Netz der Box**, wird diese
  Tunnel-Route bewusst **nicht** gesetzt — sonst würde die Box lokale Antworten
  in den Tunnel schicken und wäre unter ihrer lokalen IP nicht mehr erreichbar
  (sie würde sich praktisch selbst aussperren). Das passiert typischerweise, wenn
  beide Seiten den FRITZ!Box-Standard `192.168.178.0/24` nutzen. **Lösung:** einem
  der beiden Netze einen anderen Bereich geben (z. B. die lokale FRITZ!Box auf
  `192.168.179.0/24`) — dann ist das Ziel-Subnetz eindeutig und beides
  funktioniert gleichzeitig.
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
| „sudo: a password is required" beim Verbinden | Trat auf, weil `wg-quick` sich bei fehlenden Root-Rechten selbst per `sudo` neu startet. OffgridCloud baut WireGuard-Tunnel jetzt direkt über `ip`/`wg` auf (mit `CAP_NET_ADMIN`, ohne `sudo`) — auf die aktuelle Version aktualisieren. |
| Verbindung steht, NAS aber nicht erreichbar | Prüfen, ob in der Config `AllowedIPs` das Ziel-Subnetz enthält, und ob per IP (nicht Hostname) zugegriffen wird. |
| Bei aktivem VPN ist die Box lokal nicht mehr erreichbar | Subnetz-Kollision: lokales Netz und Heimnetz nutzen denselben Bereich (oft beide `192.168.178.0/24`). Die kollidierende Tunnel-Route wird zwar übersprungen, damit der lokale Zugriff bleibt — für gleichzeitigen NAS-Zugriff aber einem der Netze einen anderen Bereich geben (z. B. lokale FRITZ!Box auf `192.168.179.0/24`). |
