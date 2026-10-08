# local_peer_messages

Minimal Android/iOS demo app for `local_peer_connections`.

## Run with GitHub dependency (default)

```bash
flutter pub get
flutter run
```

The app follows the `main` branch at https://github.com/yanxke/local-peer-connections.

`flutter pub get` installs the revision locked in `pubspec.lock`. To update to
the current `main` branch revision, run `flutter pub upgrade local_peer_connections`.
To validate unreleased plugin edits, use the local path override below.

## Run against a local checkout

In `pubspec.yaml`, comment out the Git dependency and uncomment:

```yaml
local_peer_connections:
  path: ../local-peer-connections
```

Then run `flutter pub get` again.

If the branch has advanced but the app still compiles an older plugin commit,
refresh the locked Git revision explicitly:

```bash
flutter pub upgrade local_peer_connections
```

`flutter pub get` preserves the revision recorded in `pubspec.lock`.
