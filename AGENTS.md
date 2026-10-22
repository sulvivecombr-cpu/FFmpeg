# Working on this repo with the Base44 sandbox

FFmpeg is a C library/toolchain with no web UI. To make its behaviour visible in
the preview, the sandbox setup compiles this checkout and serves a small demo
page on port 3000 that runs the resulting `ffmpeg` / `ffprobe` binaries.

## Running it

```sh
docker compose -f docker-compose.base44.yml up -d --build
```

- `ffmpeg-build` (one-shot) runs `configure` **out of tree** in the `ffmpeg-build`
  volume and installs into the `ffmpeg-install` volume. Nothing is written into
  the repository, so `git status` stays clean during a build.
- `demo` (`base44-demo/server.py`, Python standard library only) starts only
  after the build container exits successfully and listens on host port 3000.
  It restarts itself when its own source file changes.

## Things that are not obvious

- **A full build takes several minutes** (4 cores here, `make -j4`). During a
  first boot `docker compose up -d` waits for it before starting `demo`; watch
  progress with `docker compose -f docker-compose.base44.yml logs -f ffmpeg-build`.
- **Rebuild after editing C sources** — FFmpeg is compiled, not interpreted, so
  source changes only appear after:
  ```sh
  docker compose -f docker-compose.base44.yml run --rm ffmpeg-build
  ```
  (incremental: the object tree lives in the volume) and then
  `docker compose -f docker-compose.base44.yml restart demo`.
  Editing `base44-demo/server.py` needs no rebuild.
- The sandbox build enables `--enable-gpl --enable-libx264 --enable-libvpx` so the
  demo can produce a browser-playable H.264 MP4; `--disable-doc`,
  `--disable-debug` and `--disable-ffplay` (no SDL2 in the image) keep it fast.
- `ffprobe`/`ffmpeg` are on `PATH` inside `demo`, installed at `/opt/ffmpeg/bin`.
- `palettegen` buffers until EOF, so the split/`palettegen`/`paletteuse` graph
  hangs (and is eventually OOM-killed) when fed straight from an infinite lavfi
  source. The demo writes a finite clip first, then builds the GIF from it.
- Runtime shared libraries must match the builder's Debian release: the demo
  image is `debian:bookworm-slim` like `Dockerfile.base44` (trixie ships
  `libvpx9`, but the binary links `libvpx.so.7`).
- No external service or credential is required; the demo writes to a container
  volume (`/tmp/media`), never into the checkout.

## Checks

- CI (`.github/workflows/base44-checks.yml`) validates the compose file and the
  demo server syntax on every pull request; run the same two commands locally
  before pushing.
- `curl -fsS http://localhost:3000/healthz` — demo server up.
- `docker compose -f docker-compose.base44.yml exec demo ffmpeg -version` — the
  built binary.
- FATE tests need external sample files and are not part of the sandbox setup;
  run them from a build container only when you have the samples:
  `docker compose -f docker-compose.base44.yml run --rm ffmpeg-build tests/fate-run.sh`
  style targets live in `make fate` (requires `SAMPLES`).

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `up -d` appears to hang | Expected on a first boot: `demo` waits for `ffmpeg-build` to finish. Watch with `docker compose -f docker-compose.base44.yml logs -f ffmpeg-build`. |
| Build container exits non-zero | Read its log — usually a compile/configure error in the checked-out tree or a full disk. Fix the code, then re-run `run --rm ffmpeg-build`; the object tree in the volume is reused. |
| `demo` restart loops with `error while loading shared libraries: libvpx.so.7` / `libx264.so.164` | Builder and runtime images drifted to different Debian releases. Keep both on `debian:bookworm-slim` and `docker compose -f docker-compose.base44.yml build demo`. |
| Page loads but every encode fails | `/api/info` reporting `ffmpeg not found` means the install volume is empty: re-run the `ffmpeg-build` service. |
| Encode hangs, container killed later | `palettegen` buffers until EOF — never feed it an infinite lavfi source (see above). |
| Port 3000 already in use | `docker compose -f docker-compose.base44.yml ps`, then `down` the other stack or stop the other process. |
| Full reset (drops build cache and demo media) | `docker compose -f docker-compose.base44.yml down -v`, then `up -d --build`. |

## Not for production

This is a development preview. `demo` has no authentication, binds `0.0.0.0:3000`
and accepts unauthenticated encode requests, so never expose it publicly as-is.
