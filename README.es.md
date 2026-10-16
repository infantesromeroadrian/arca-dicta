# ARCA Dicta

Dictado técnico para Hyprland, desarrollado como complemento de
[hyprwhspr](https://github.com/goodroot/hyprwhspr).

[English](README.md) · [Instalación y reversión](docs/installation.es.md) · [Arquitectura](docs/architecture/pipeline.html) · [CI y artefactos de prueba](https://github.com/infantesromeroadrian/arca-dicta/actions/workflows/ci.yml)

**Snapshot de desarrollo: 0.1.0-alpha.1. Todavía no hay GitHub Release ni instalador publicado como release.**

Combina un preview local provisional, transcripción final mediante Groq,
revisión bilingüe obligatoria en Ollama Cloud, HUD legible y silencio reversible
del audio de reproducción. Está orientado a español, inglés y sus mezclas.
Si falla la revisión, retiene un dictado en memoria para reintentar o descartar;
nunca pega el texto sin revisar ni envía Enter automáticamente.

El destino inicial es Arch/CachyOS x86_64 con Hyprland, hyprwhspr **1.49.0-1**
y Python **3.14**. El archivo distribuible contiene código, instalador y
manifiestos; las dependencias y el modelo se preparan en el equipo. No incluye
pesos, entornos Python, credenciales ni un producto Docker.

## Probar el snapshot de desarrollo

Construye el archivo desde este checkout o descarga `source-installer-<SHA del commit>`
de una ejecución correcta de [Actions](https://github.com/infantesromeroadrian/arca-dicta/actions/workflows/ci.yml),
cuando esté disponible. Es un artefacto de pruebas, no una alpha aceptada ni una
GitHub Release. La [guía](docs/installation.es.md) explica la construcción y
verificación. Desde el archivo verificado y extraído:

```sh
./install install --download-model
./install doctor --no-service
```

La preparación deja intactos el servicio y la configuración del escritorio.
La activación posterior es explícita: `./install activate`. Si existen overrides
del servicio, exige `--adopt-existing`, los respalda y los conserva. Utiliza la
única unidad `hyprwhspr.service`; no instala una segunda. `deactivate` recupera
la configuración y entrada anteriores; `uninstall` elimina únicamente archivos
propios sin modificar. Los bindings globales se configuran conscientemente.

Consulta la [guía completa](docs/installation.es.md) para dependencias,
credenciales, límites, diagnóstico y reversión. Los tests offline utilizan
proveedores y dispositivos simulados; no demuestran dictado físico, calidad
de los proveedores ni latencias de escritorio.

## Alcance verificado

La ejecución local registrada en Arch pasó 177 tests, Ruff, ShellCheck, los
26 contratos upstream y dos builds idénticos byte a byte. Un usuario nuevo de
contenedor completó instalación, inferencia de voz sintética con el worker
local, preparación idempotente y desinstalación. La activación real pasó doctor
35/35; la reversión recuperó los 25 archivos y modos del baseline, con mute y
volumen intactos en cuatro salidas. Véanse la [evidencia CI y sus hashes](docs/validation/ci.md)
y el [registro de activación y reversión](docs/validation/installer-baseline.md).

El usuario aceptó el uso personal del runtime existente, que se mantiene
separado. Quedan por validar el dictado físico del paquete en editor y navegador,
los fallos, la cancelación y la restauración audible, además de medir la latencia
y preparar la demo. La GitHub Release sigue pendiente. La comprobación de
activación no inició grabaciones ni llamadas a proveedores. Estos resultados
no acreditan precisión semántica
ni latencia de escritorio. El estado remoto de CI se consulta en la ejecución
de Actions correspondiente al commit exacto.

## Comportamiento y límites

El preview usa faster-whisper 1.2.1, CTranslate2 4.8.0 y el snapshot small
`536b0662742c02347bc0e980a01041f333bce120`, CPU/int8. El instalador comprueba
cuatro archivos: `model.bin`, `config.json`, `tokenizer.json` y `vocabulary.txt`.
No se conserva historial de audio o transcripciones. Los datos de IPC temporales
del HUD siguen el mecanismo nativo de hyprwhspr.

La revisión final usa `deepseek-v4.1-flash` de Ollama Cloud. La falta de clave,
un timeout o una revisión inválida bloquean la entrega. Los 12 hashes core
y 14 del HUD fijan el contrato privado con upstream; un cambio core impide
arrancar el daemon adaptado. El CLI nativo directo queda fuera de esa garantía.

El plan canónico reside en Obsidian, `30-Proyectos/ARCA-Dicta/plan.md`.
Procedencia y licencias: [extracción](docs/provenance/extraction.md),
[dependencias](docs/provenance/dependencies.md),
[avisos de terceros](THIRD_PARTY_NOTICES.md) y [MIT](LICENSE).
