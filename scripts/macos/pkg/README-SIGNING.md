# Firma e notarizzazione macOS (opzionale)

Senza questo, Gatekeeper mostra *“Apple could not verify…”* sui `.pkg` scaricati da internet.  
Con un account [Apple Developer](https://developer.apple.com/) (~99 USD/anno) si elimina l’avviso.

## Certificati necessari

1. **Developer ID Application** — firma `WattbikeLogger.app`
2. **Developer ID Installer** — firma il `.pkg`
3. App-specific password per **notarytool** (appleid.apple.com → Accesso e sicurezza)

Esporta i due certificati dal Keychain in un `.p12`, codifica in base64:

```bash
base64 -i Certificates.p12 | pbcopy
```

## Secret GitHub (Settings → Secrets → Actions)

| Secret | Contenuto |
|--------|-----------|
| `MACOS_CERTIFICATE_P12` | contenuto base64 del `.p12` |
| `MACOS_CERTIFICATE_PASSWORD` | password del `.p12` |
| `APPLE_TEAM_ID` | Team ID (10 caratteri) |
| `APPLE_ID` | Apple ID usato per notarizzare |
| `APPLE_APP_SPECIFIC_PASSWORD` | password app-specific |
| `MACOS_SIGN_IDENTITY_APP` | es. `Developer ID Application: Nome (TEAMID)` |
| `MACOS_SIGN_IDENTITY_INSTALLER` | es. `Developer ID Installer: Nome (TEAMID)` |

Quando questi secret sono presenti, il workflow `Build & Release` firma e notarizza automaticamente il `.pkg`.

## Test locale

```bash
export MACOS_SIGN_IDENTITY_APP="Developer ID Application: …"
export MACOS_SIGN_IDENTITY_INSTALLER="Developer ID Installer: …"
export APPLE_ID="…"
export APPLE_TEAM_ID="…"
export APPLE_APP_SPECIFIC_PASSWORD="…"
./scripts/macos/pkg/build_pkg.sh
```
