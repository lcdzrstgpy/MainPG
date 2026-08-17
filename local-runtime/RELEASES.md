# MainPG Windows release publishing

`build_installer.ps1` has one release-version input. For version `1.1.0`, it
generates runtime metadata, `MainPG-Setup-1.1.0.exe`, and
`MainPG-portable-1.1.0.zip` from that same value.

Build a signed update release with a private Ed25519 key stored outside this
repository and outside the output directory:

```powershell
$env:MAINPG_RELEASE_SIGNING_KEY_PATH = 'C:\secure\mainpg-release-ed25519.pem'
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1 `
  -Version 1.1.0 `
  -InstallerUrl 'https://workbench.haocoming.top/mainpg/windows/MainPG-Setup-1.1.0.exe' `
  -ReleaseNotes 'Bug fixes and stability improvements'
```

The key file can be an unencrypted Ed25519 PEM private key or a file containing
a base64-encoded 32-byte Ed25519 seed. The build fails when `-InstallerUrl` is
provided without `MAINPG_RELEASE_SIGNING_KEY_PATH`; it never copies the key into
`dist` or the PyInstaller bundle.

Upload these public static paths together:

```
/mainpg/windows/MainPG-Setup-1.1.0.exe
/mainpg/windows/manifest.json
```

The desktop release origin is the explicit `UPDATE_RELEASE_HOST` constant in
`wh_local/config.py`. Change that constant only when the official distribution
host moves; the built-in manifest URL is
`https://workbench.haocoming.top/mainpg/windows/manifest.json`.

Configure the update service with the matching Ed25519 **public** key. The
canonical `release-manifest.json` (uploaded as `manifest.json`) contains `version`, `mandatory`,
`installer_url`, `sha256`, `release_notes`, `published_at`, and `signature`.
The signature is base64 Ed25519 over sorted-key, compact UTF-8 JSON containing
every field except `signature`. Do not hand-author or publish a placeholder
signature.
