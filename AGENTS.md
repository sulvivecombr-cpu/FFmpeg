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

- `curl -fsS http://localhost:3000/healthz` — demo server up.
- `docker compose -f docker-compose.base44.yml exec demo ffmpeg -version` — the
  built binary.
- FATE tests need external sample files and are not part of the sandbox setup;
  run them from a build container only when you have the samples:
  `docker compose -f docker-compose.base44.yml run --rm ffmpeg-build tests/fate-run.sh`
  style targets live in `make fate` (requires `SAMPLES`).
