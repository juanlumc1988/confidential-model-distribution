# Plan: tests unitarios + CI

Estado actual (revisado 2026-10-13, contrastado con el código):

- ~1000 líneas de Python 3 (`shared/`, `producer/`, `consumer/`) + `setup/setup-dev-env.sh`.
- **Sin tests.** **Sin CI** (no existe `.github/`).
- Dependencias fijadas con hash (`requirements.lock`, `uv pip compile`); imagen base `python@sha256:…` fijada por digest = `python:3.12.14-slim-trixie` (verificado en el índice de Docker Hub y en el `PYTHON_VERSION` de la config amd64).
- La verificación actual es manual, end-to-end en kind (`manifests/verify/*`: sin clave, clave errónea, artefacto manipulado).

## 1. Tests unitarios (pytest), sin red ni clúster

### `shared/artifact_format.py`
Formato: `nonce(12) || ciphertext || tag(16)`. **No hay cabecera**: `FORMAT_VERSION` solo entra en la AAD.
- Round-trip `encrypt` → `decrypt`; clave de 32 bytes; nonce distinto en dos llamadas.
- `associated_data("x") == b"cmd/v1|x"`.
- `InvalidTag` con: clave errónea, AAD distinta (otro repo o otra versión), un bit cambiado en nonce, ciphertext o tag, blob truncado (≥ 28 bytes).
- `ValueError` con blob < 28 bytes (`NONCE_BYTES + TAG_BYTES`).

### `producer/build_artifact.py`
- `_normalise`: uid/gid=0, uname/gname vacíos, mtime=0, modo 0755 en directorios y 0644 en ficheros.
- `build_tar` determinista: mismos bytes tras cambiar mtime/permisos del origen; entradas ordenadas; rutas relativas.
- `build` con `fetch_and_normalise` mockeado: el resultado se descifra con la AAD del repo; el directorio temporal desaparece.

### `producer/main.py`
- `parse_args`: valores por defecto y flags (`--skip-upload`, `--skip-secret`, `--out-dir`, `--namespace`).
- `write_local`: escribe `artifact.enc` y `model.key` en **bytes crudos** (no base64), con modo `0600` desde la creación (comprobar con `umask(0)`).
- `main`: devuelve 2 sin `HF_TOKEN` y sin `--skip-upload`; devuelve 3 si `preflight` lanza `PreflightError`; con `--skip-upload --skip-secret` no llama a la red.

### `producer/publish.py`
- `render_model_card` con una plantilla en `tmp_path`.
- `current_namespace`: fichero con contenido, vacío (→ default) y ausente (→ default). Hay que parchear `NAMESPACE_FILE`.
- `store_key_in_secret` (con `kubernetes` mockeado): create OK; un 409 lleva a `replace`; cualquier otro status se relanza; `data["model.key"]` es el base64 de la clave.
- `preflight` → `PreflightError` (con el cliente HF mockeado).

### `consumer/consume.py`
- `read_key`: acepta 32 bytes crudos, **incluidos los que empiezan o acaban en `0x0a`/`0x20`** (no hace `.strip()`, y es deliberado); `ValueError` con 0, 31 o 33 bytes; `FileNotFoundError` si no existe.
- `extract` (`filter="data"`): rechaza `../`, symlinks que salen del destino y ficheros de dispositivo (`tarfile.FilterError`); extrae bien un tar generado por `build_tar`. **Corrección (verificado al implementar):** las rutas absolutas **no** se rechazan; el filtro quita la `/` inicial y las extrae dentro del destino, que es seguro. El test comprueba ese confinamiento.
- `main` (con `download_artifact` y `load_model` mockeados) devuelve 1 en estos casos: sin clave, clave con longitud incorrecta, fallo de descarga, `InvalidTag` y artefacto corto. Con artefacto válido devuelve 0. En los casos de fallo hay que comprobar además que no se ha extraído nada en `work_dir/model`.

### Requisitos
- `tests/` en la raíz y un `conftest.py` que meta `shared/`, `producer/` y `consumer/` en `sys.path`. Así reproduce el `PYTHONPATH=/app/shared` de las imágenes y el import entre módulos hermanos de `producer/` (`main` importa `build_artifact` y `publish`).
- Dependencias de test en `requirements-dev.in`, con su lock con hash: `pytest`, `pytest-cov`, `cryptography` (la misma versión que en los locks).
- No hace falta instalar `torch`/`transformers` para los tests unitarios: `huggingface_hub`, `kubernetes` y `transformers` se importan dentro de las funciones, así que basta con mockearlos con `monkeypatch`/`sys.modules`.
- Cobertura: 100 % en `artifact_format.py` y al menos 80 % en total (sin contar `load_model`, `download_artifact`, `upload_artifact` ni `fetch_and_normalise`, que se cubren en el e2e).

## 2. CI (GitHub Actions, `.github/workflows/ci.yml`)

En `push` y `pull_request`, jobs en paralelo:
1. **lint**: `ruff check` + `ruff format --check`; `shellcheck setup/setup-dev-env.sh`.
2. **test**: `actions/setup-python` con `python-version: 3.12.14` (la versión de la imagen, verificada; trae `tarfile` con `filter=` de serie), `pip install --require-hashes -r requirements-dev.lock` y `pytest --cov`.
3. **docker-build**: build de las dos imágenes desde la raíz (`docker build -f producer/Dockerfile .`), sin push.
4. **manifests**: `kubeconform` sobre `manifests/` (incluido `verify/`).
5. **lock-check**: `uv pip compile` de cada `requirements.in` y `git diff --exit-code` sobre los locks.
6. *(opcional)* **e2e**: kind + producer + `manifests/verify/*`. Necesita el secreto `HF_TOKEN`, así que solo con `workflow_dispatch` y nunca en PRs desde forks.

Requisitos transversales:
- Acciones fijadas por SHA, coherente con los digests de Docker.
- `permissions: contents: read` a nivel de workflow.
- Dependabot para `github-actions`, `pip` (los dos directorios) y `docker`.

## 3. Hallazgos de la revisión (fuera de tests/CI, a decidir)

- `consume.main` no captura las excepciones de `extract` (`tarfile.FilterError`, `OSError`) ni de `load_model`. El comportamiento sigue siendo fail-closed (sale con código ≠ 0), pero con traceback en vez de la línea `[x] …` que usan el resto de errores, contra lo que el propio código dice buscar. Propuesta: capturarlas y devolver `fail(...)`, con su test.
- ~~La versión de Python solo se conoce por el digest.~~ **Hecho:** documentada en los dos Dockerfiles; `requirements-dev.lock` se compila con `--python-version 3.12` y con los locks de las imágenes como constraints (cryptography, cffi, pycparser y packaging tienen la misma versión en los tres locks).

## Orden sugerido

1. Tests de `artifact_format.py`, `read_key`, `extract` y `consume.main` (el núcleo de seguridad y fail-closed).
2. Workflow con lint y test, con la versión de Python fijada.
3. Resto de tests unitarios.
4. docker-build, manifests, lock-check y Dependabot.
5. e2e manual.

## Estado de implementación

- [x] Tests unitarios (61) de `shared/`, `consumer/` y `producer/`; cobertura total 94 %. Sin cubrir: `download_artifact` y `load_model` (los cubre el e2e).
- [x] `requirements-dev.in`/`.lock`: Python 3.12, locks de imagen como constraints, ruff 0.16.8 fijado.
- [x] `ruff.toml` con reglas explícitas (E, W, F, I, B). El código fuente se ha formateado con `ruff format` (solo cambios de saltos de línea).
- [x] `.github/workflows/ci.yml`: lint (ruff + shellcheck), test (3.12.14, cobertura ≥ 90 %), lock-check (lock de desarrollo), manifests (kubeconform v0.8.0 con checksum), docker-build (producer y consumer).
- [x] `.github/dependabot.yml`: github-actions y docker. pip queda fuera: Dependabot no regenera locks de uv con hashes.
- [ ] Lock-check de los locks de imagen: no verificado (el proxy de la sesión bloquea `download.pytorch.org`). Pendiente de confirmar que `uv pip compile` los reproduce idénticos y, en tal caso, añadirlos al job.
- [ ] e2e manual (`workflow_dispatch`).
- [ ] Hallazgo: `write_local` no corrige los permisos de un `model.key` ya existente (`O_CREAT` solo aplica `0600` al crear). Si existía con `0644`, se queda en `0644`. Corrección propuesta: `os.fchmod(fd, 0o600)` tras `os.open`, con su test.
- [ ] Hallazgo: `consume.main` no captura los errores de `extract`/`load_model` (ver §3).
