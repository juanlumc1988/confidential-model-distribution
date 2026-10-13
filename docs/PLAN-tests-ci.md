# Plan: tests unitarios + CI

Estado actual (revisado 2026-10-13):

- ~1000 líneas de Python 3 (`shared/`, `producer/`, `consumer/`) + `setup/setup-dev-env.sh`.
- **Sin tests.** **Sin CI** (no existe `.github/workflows/`).
- Dependencias fijadas con hash (`requirements.lock`, `uv pip compile`); imágenes Docker fijadas por digest.
- La verificación actual es manual, end-to-end en kind (`manifests/verify/*`: sin clave, clave errónea, artefacto manipulado).

## 1. Tests unitarios (pytest), sin red ni clúster

| Módulo | Qué probar |
| --- | --- |
| `shared/artifact_format.py` | round-trip `encrypt`/`decrypt`; fallo con clave errónea, AAD distinta (otro `hf_repo_id`), blob truncado, tag/nonce/cabecera alterados, `FORMAT_VERSION` desconocida; longitudes `KEY_BYTES`/`NONCE_BYTES`/`TAG_BYTES`; nonce distinto en cada llamada. |
| `producer/build_artifact.py` | `build_tar` determinista (mismos bytes en dos ejecuciones; `_normalise` anula uid/gid/mtime/modo); `build` con `fetch_and_normalise` mockeado y `self_check`. |
| `producer/publish.py` | `render_model_card`; `current_namespace` (fichero presente/ausente, vía `tmp_path`); `preflight` lanza `PreflightError` (cliente HF mockeado); `store_key_in_secret` crea vs. actualiza (cliente k8s mockeado, 409 → replace). |
| `producer/main.py` | `parse_args` (defaults, obligatorios); `write_local` (contenido y permisos `0600` de la clave). |
| `consumer/consume.py` | `read_key` (ausente, vacía, longitud incorrecta, base64/raw según formato); `extract` rechaza path traversal (`../`, rutas absolutas, symlinks); `main` devuelve código ≠ 0 en cada modo de fallo (fail-closed), con `download_artifact`/`load_model` mockeados. |

Requisitos:
- `tests/` en raíz, `conftest.py` que añada `shared/` al `sys.path` (igual que hacen las imágenes).
- Dependencias de test separadas (`requirements-dev.in` → lock con hash): `pytest`, `pytest-cov`.
- Mock de `huggingface_hub` y `kubernetes`; ningún test toca red.
- Objetivo de cobertura: 100 % en `artifact_format.py`, ≥ 80 % global.

## 2. CI (GitHub Actions, `.github/workflows/ci.yml`)

En `push` y `pull_request`, jobs en paralelo:
1. **lint**: `ruff check` + `ruff format --check`; `shellcheck setup/setup-dev-env.sh`.
2. **test**: Python de la misma versión que la imagen base; instalar locks con `--require-hashes`; `pytest --cov`.
3. **docker-build**: `docker build` de `producer/` y `consumer/` (sin push), verifica que los locks y Dockerfiles siguen siendo válidos.
4. **manifests**: `kubeconform` sobre `manifests/`.
5. *(opcional, manual / nightly)* **e2e**: kind + `manifests/verify/*`. Necesita secreto `HF_TOKEN` → solo `workflow_dispatch`, nunca en PRs de forks.

Requisitos transversales:
- Acciones fijadas por SHA (coherente con los digests de Docker).
- `permissions: contents: read` mínimo.
- Comprobar que `requirements.lock` coincide con `requirements.in` (`uv pip compile` + `git diff --exit-code`).
- Dependabot para actions, pip y Docker.

## Orden sugerido

1. Tests de `artifact_format.py` + `extract`/`read_key` (núcleo de seguridad).
2. Workflow con lint + test.
3. Resto de tests unitarios.
4. docker-build, manifests, dependabot.
5. e2e manual.
