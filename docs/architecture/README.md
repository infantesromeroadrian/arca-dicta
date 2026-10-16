# Pipeline de ARCA Dicta / ARCA Dicta pipeline

[Abrir el diagrama / Open the diagram](pipeline.html) · [Fuente / Source](pipeline.json)

La vista describe el recorrido implementado de audio y texto, con referencias
al código publicado en `1cf0e6432c460ad4f1603df6392e796a10879b52`. El audio final
sale a Groq; la transcripción pasa a Ollama para su revisión y a las guardas
locales antes del pegado. La conexión con Ollama representa la petición de texto
y la respuesta JSON. La previsualización local reutiliza la captura existente:
su borrador se muestra en el HUD y nunca se convierte en el texto final pegado.

El servicio personal está operativo. Esta extracción y su instalador todavía
necesitan completar la validación física como producto. La vista no acredita un
despliegue ni una release. La instalación prepara entornos de usuario separados;
la activación aplica un cambio reversible a la única unidad `hyprwhspr.service`.
No se despliega una segunda instancia ni un servicio cloud propio.

El contenido del diagrama está en español. Los controles fijos del visor y su
atributo HTML de idioma usan inglés, el valor de reserva de Archify. El archivo
es autónomo, sin cargar recursos externos al abrirlo; los enlaces de código
abren GitHub cuando se seleccionan.

The diagram describes the implemented audio and text pipeline, with evidence
pinned to the source revision above. Final audio goes to Groq; the transcript
goes to Ollama, then local validation gates delivery. The Ollama connection
represents both the text request and JSON response. Local preview reuses the
existing capture and never supplies the final pasted text.

The personal service is operational. Physical validation of the extracted
product and installer is still pending; this diagram does not certify a
deployment or release. Preparation creates separate user environments;
activation reversibly adapts the single `hyprwhspr.service`. There is no second
daemon or project-owned cloud service. Diagram labels are Spanish; fixed viewer
controls and the HTML language attribute default to English.

The standalone viewer is generated with Archify 2.16 and includes third-party
MIT code. See [its retained license](../../LICENSES/archify-MIT.txt).
